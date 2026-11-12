# FX Client Profitability Engine

A Dockerised data engine that turns a synthetic spot-FX trade book into client-level profitability metrics.

**Work in progress.** See the original brief in [docs/brief.md](docs/brief.md).

## Setup

The only host requirement is Docker.

```sh
cp .env.example .env   # then set FX_DB_PASSWORD in .env (it is gitignored)
make up                # start Postgres
make pipeline          # ECB snapshot -> generate -> load -> dbt build
```

Run `make help` for the other targets.
