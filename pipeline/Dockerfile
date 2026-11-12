FROM python:3.12.16-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Non-root user. Code is bind-mounted over /app at run time, so the editable
# install below must live at the same path as the mount.
RUN useradd --create-home --uid 1000 fx
WORKDIR /app

COPY requirements.lock ./
RUN pip install -r requirements.lock

COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-deps -e .

USER fx
CMD ["python", "-m", "fxengine", "--help"]
