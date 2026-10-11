FROM node:24.20.0-bookworm-slim@sha256:6642ef280aebc09c4541bee0b15c9f89f0f3f3c247ddee79ae1d37eddfdcbbaa AS build

WORKDIR /app
RUN corepack enable && corepack prepare pnpm@11.21.0 --activate
COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile --ignore-scripts
COPY frontend/ ./
RUN pnpm run build

FROM nginxinc/nginx-unprivileged:1.30.4-alpine@sha256:adf5042a17f4ecdd200c595fa9ffd1be37efb18f89a830bd1a00e4ab4d59d42c AS runtime

USER root
RUN rm -f /sbin/apk && rm -rf /docker-entrypoint.d /docker-entrypoint.sh /etc/nginx/conf.d
COPY --chmod=0444 docker/frontend/nginx.conf /etc/nginx/nginx.conf
COPY --chmod=0555 docker/frontend/entrypoint.sh /usr/local/bin/frontend-entrypoint.sh
COPY --from=build --chmod=0555 /app/dist/ /usr/share/nginx/html/
USER 101:101
EXPOSE 8080
HEALTHCHECK --interval=5s --timeout=3s --start-period=5s --retries=10 CMD wget -q -O /dev/null http://127.0.0.1:8080/healthz || exit 1
ENTRYPOINT ["sh", "/usr/local/bin/frontend-entrypoint.sh"]
