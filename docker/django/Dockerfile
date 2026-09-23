ARG SOURCE_DATE_EPOCH=0

FROM python:3.14.6-slim@sha256:7bec7ddcddeff7975d6ba9b4be7dd6f6b2f55e7491539145e2978f7f97ce9144 AS base

RUN apt-get update \
    && apt-get install --no-install-recommends --assume-yes \
        base-files=13.8+deb13u7 \
        bash=5.2.37-2+b10 \
        bsdutils=1:2.41.5-0+deb13u1 \
        gzip=1.13-1+deb13u1 \
        libaudit-common=1:4.0.2-2+deb13u1 \
        libaudit1=1:4.0.2-2+deb13u1 \
        libblkid1=2.41.5-0+deb13u1 \
        libc-bin=2.41-12+deb13u4 \
        libc6=2.41-12+deb13u4 \
        libcap2=1:2.75-10+deb13u1+b3 \
        liblastlog2-2=2.41.5-0+deb13u1 \
        libmount1=2.41.5-0+deb13u1 \
        libpcre2-8-0=10.46-1~deb13u2 \
        libsmartcols1=2.41.5-0+deb13u1 \
        libsqlite3-0=3.46.1-7+deb13u2 \
        libssl3t64=3.5.7-1~deb13u2 \
        libuuid1=2.41.5-0+deb13u1 \
        login=1:4.16.0-2+really2.41.5-0+deb13u1 \
        mount=2.41.5-0+deb13u1 \
        openssl=3.5.7-1~deb13u2 \
        openssl-provider-legacy=3.5.7-1~deb13u2 \
        perl-base=5.40.1-6+deb13u1 \
        tzdata=2026c-0+deb13u1 \
        util-linux=2.41.5-0+deb13u1 \
    && rm -rf /var/lib/apt/lists/*

FROM base AS runtime-source-normalizer

WORKDIR /normalized

COPY --chmod=0555 src ./src
COPY --chmod=0555 scripts ./scripts
COPY --chmod=0555 docker/django/entrypoint.sh ./docker/django/entrypoint.sh
COPY --chmod=0444 pyproject.toml uv.lock ./

RUN find /normalized -exec touch --no-dereference --date=@0 {} +

FROM runtime-source-normalizer AS test-source-normalizer

COPY --chmod=0555 tests ./tests
COPY --chmod=0555 docs ./docs
COPY --chmod=0555 docker ./docker
COPY --chmod=0444 .dockerignore .sops.yaml .gitleaks.toml ./
COPY --chmod=0444 compose.yaml compose.development.yaml compose.testing.yaml ./
COPY --chmod=0444 .env.example .env.development.sops .env.testing.sops ./

RUN find /normalized -exec touch --no-dereference --date=@0 {} +

FROM base AS builder

COPY --from=ghcr.io/astral-sh/uv:0.12.1@sha256:cf4eedcaa81655197f625739489effcbe71b61ceb1506f332c3facae5deceded /uv /usr/local/bin/uv

ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

COPY --from=runtime-source-normalizer /normalized/pyproject.toml /normalized/uv.lock ./

RUN uv sync --frozen --no-dev --no-install-project

FROM builder AS test-builder

RUN uv sync --frozen --no-install-project

FROM base AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/src \
    PATH=/opt/venv/bin:$PATH

RUN groupadd --system --gid 10001 localforge \
    && useradd --system --uid 10001 --gid 10001 --home-dir /app --no-create-home localforge

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app

COPY --from=runtime-source-normalizer --chown=root:root /normalized/src ./src
COPY --from=runtime-source-normalizer --chown=root:root /normalized/scripts ./scripts
COPY --from=runtime-source-normalizer --chown=root:root /normalized/docker/django/entrypoint.sh /usr/local/bin/entrypoint.sh

RUN chmod 0755 /usr/local/bin/entrypoint.sh \
    && chmod -R a-w /app \
    && install -d -o localforge -g localforge -m 0755 /app/staticfiles \
    && find /app -exec touch --no-dereference --date=@0 {} +

USER localforge

EXPOSE 8000

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]

FROM runtime AS test

USER root

COPY --from=test-builder /opt/venv /opt/venv

COPY --from=test-source-normalizer --chown=root:root /normalized/tests ./tests
COPY --from=test-source-normalizer --chown=root:root /normalized/docs ./docs
COPY --from=test-source-normalizer --chown=root:root /normalized/docker ./docker
COPY --from=test-source-normalizer --chown=root:root /normalized/pyproject.toml /normalized/uv.lock /normalized/.dockerignore /normalized/.sops.yaml /normalized/.gitleaks.toml ./
COPY --from=test-source-normalizer --chown=root:root /normalized/compose.yaml /normalized/compose.development.yaml /normalized/compose.testing.yaml ./
COPY --from=test-source-normalizer --chown=root:root /normalized/.env.example /normalized/.env.development.sops /normalized/.env.testing.sops ./

RUN install -d -o localforge -g localforge -m 0755 /app/test-results /app/htmlcov /app/.pytest_cache \
    && install -o localforge -g localforge -m 0644 /dev/null /app/coverage.xml \
    && touch --no-dereference --date=@0 \
        /app \
        /app/test-results \
        /app/htmlcov \
        /app/.pytest_cache \
        /app/coverage.xml

USER localforge

ENV DJANGO_SETTINGS_MODULE=config.settings.testing \
    COVERAGE_FILE=/tmp/.coverage

ENTRYPOINT ["poe"]

CMD ["test"]
