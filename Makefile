# Everything runs in containers. The only host requirement is Docker.

COMPOSE  := docker compose
PIPELINE := $(COMPOSE) run --rm pipeline
# Targets that never touch Postgres skip the db dependency.
PIPELINE_NODB := $(COMPOSE) run --rm --no-deps pipeline
DBT      := $(COMPOSE) run --rm dbt

.DEFAULT_GOAL := help

.PHONY: help up down clean build pipeline dbt dbt-debug dbt-docs \
        test test-slow test-integration lint refresh-ecb param-sheet psql

help: ## List targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F ':.*## ' '{printf "  %-18s %s\n", $$1, $$2}'

up: ## Start Postgres and wait until it is healthy
	$(COMPOSE) up -d --wait db

down: ## Stop containers (data volume is kept)
	$(COMPOSE) down

clean: ## Remove containers and the fxengine_ volumes (asks first)
	@printf 'This deletes the fxengine_ volumes, including all Postgres data. Continue? [y/N] '; \
	read answer; \
	if [ "$$answer" = "y" ] || [ "$$answer" = "Y" ]; then \
		$(COMPOSE) down -v --remove-orphans; \
	else \
		echo "Aborted."; \
	fi

build: ## Build the pipeline and dbt images
	$(COMPOSE) build

pipeline: ## Run the full pipeline: ECB snapshot -> generate -> load -> dbt build
	$(PIPELINE) python -m fxengine run-all
	$(DBT) build

dbt: ## Run dbt build (models and tests)
	$(DBT) build

dbt-debug: ## Check the dbt connection to Postgres
	$(DBT) debug

dbt-docs: ## Generate and serve dbt docs on 127.0.0.1:8081 (override with FX_DBT_DOCS_PORT)
	$(DBT) docs generate
	# --host 0.0.0.0 binds inside the container only; docker-compose.yml publishes the port on
	# 127.0.0.1 alone, so the docs are not reachable from other machines.
	$(COMPOSE) run --rm --service-ports dbt docs serve --host 0.0.0.0 --port 8081 --no-browser

test: ## Fast unit tests (not slow, not integration)
	$(PIPELINE_NODB) pytest -m "not slow and not integration"

test-slow: ## Slow tests
	$(PIPELINE_NODB) pytest -m slow

test-integration: ## Integration tests (starts the db; writes only the raw_test schema, never raw)
	$(PIPELINE) pytest -m integration

lint: ## Ruff
	$(PIPELINE_NODB) ruff check .

refresh-ecb: ## Download ECB rates; a new month writes a new snapshot file, which run-all uses as the latest
	$(PIPELINE_NODB) python -m fxengine fetch-ecb

param-sheet: ## Render docs/methodology/parameter-sheet.md
	$(PIPELINE_NODB) python -m fxengine param-sheet

psql: up ## Open psql in the db container
	$(COMPOSE) exec db sh -c 'psql -U "$$POSTGRES_USER" -d "$$POSTGRES_DB"'
