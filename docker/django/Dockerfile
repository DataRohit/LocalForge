FROM python:3.14.6-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.12.1 /uv /usr/local/bin/uv

ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

COPY pyproject.toml uv.lock ./

RUN uv sync --frozen --no-dev --no-install-project

FROM builder AS test-builder

RUN uv sync --frozen --no-install-project

FROM python:3.14.6-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/src \
    PATH=/opt/venv/bin:$PATH

RUN groupadd --system --gid 10001 localforge \
    && useradd --system --uid 10001 --gid 10001 --home-dir /app --no-create-home localforge

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app

COPY --chown=root:root src ./src
COPY --chown=root:root scripts ./scripts
COPY --chown=root:root docker/django/entrypoint.sh /usr/local/bin/entrypoint.sh

RUN chmod 0755 /usr/local/bin/entrypoint.sh \
    && chmod -R a-w /app \
    && install -d -o localforge -g localforge -m 0755 /app/staticfiles

USER localforge

EXPOSE 8000

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]

FROM runtime AS test

USER root

COPY --from=test-builder /opt/venv /opt/venv

COPY --chown=root:root tests ./tests
COPY --chown=root:root docs ./docs
COPY --chown=root:root docker ./docker
COPY --chown=root:root pyproject.toml uv.lock .sops.yaml ./
COPY --chown=root:root compose.yaml compose.development.yaml compose.testing.yaml ./
COPY --chown=root:root .env.example .env.development.sops .env.testing.sops ./

RUN install -d -o localforge -g localforge -m 0755 /app/test-results /app/htmlcov /app/.pytest_cache \
    && install -o localforge -g localforge -m 0644 /dev/null /app/coverage.xml

USER localforge

ENV DJANGO_SETTINGS_MODULE=config.settings.testing \
    COVERAGE_FILE=/tmp/.coverage

ENTRYPOINT ["poe"]

CMD ["test"]
