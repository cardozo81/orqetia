ARG PYTHON_BASE=python:3.14.3-slim

FROM ${PYTHON_BASE} AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

RUN python -m pip install --no-cache-dir "uv==0.10.0"

COPY pyproject.toml uv.lock README.md ./
COPY src ./src

RUN uv sync --locked --no-dev --no-editable --python 3.14

FROM ${PYTHON_BASE} AS runtime

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN groupadd --gid 10001 orqetia \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin orqetia

COPY --from=builder --chown=10001:10001 /app/.venv /app/.venv
COPY --chown=10001:10001 alembic.ini ./
COPY --chown=10001:10001 migrations ./migrations
COPY --chown=10001:10001 apps ./apps
COPY --chown=10001:10001 contracts ./contracts

USER 10001:10001

CMD ["uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers"]
