# Frontend deployment candidates

These files are inert. Ticket 97 builds and exercises the image outside both Compose projects.
Ticket 99 supplies deployment and rollback commands; no candidate is loaded automatically by operator tooling.

## Ownership and proof

- `docker/frontend/Dockerfile` builds `localforge/frontend:0.1.0` with pinned Node 24.20.0 and pnpm 11.21.0.
  Runtime is digest-pinned nginx-unprivileged 1.30.4, UID/GID 101, with no Node, npm, pnpm, or apk executable.
- `compose.candidate.yaml` adds only `frontend-fe6vn`, edge network only, no host publication, secret, or volume.
  It requires read-only root, dropped capabilities, no-new-privileges, and a bounded non-executable `/tmp` tmpfs.
- Django's exact paths and owned prefixes have priority 100; the SPA has priority 10. Existing public admin
  blocking and forwarded-HTTPS middleware stay on Django. Frontend health is `/healthz` on port 8080.
- `registry.candidate.patch` updates operator and naming registries when deployment is authorized. Apply it
  together with the Compose candidate, not before; until then existing development gates retain their baseline.
- Alloy already discovers every container with `com.docker.compose.project=localforge-dev`, attaches service
  labels, and sends its stdout/stderr to Loki. No collection allowlist or additional collector is needed.
  Access records are JSON and omit query strings. The entrypoint converts native nginx error records to
  escaped JSON with their original severity through a transient FIFO; nginx remains PID 1. Both records
  go to stdout and the existing JSON extraction assigns their level and logger labels.

```console
make frontend-image
make frontend-image-check
git apply --check docs/runbooks/frontend-deployment/registry.candidate.patch
docker compose --project-name localforge-dev --env-file .env.development -f compose.yaml -f compose.development.yaml -f docs/runbooks/frontend-deployment/compose.candidate.yaml config --quiet
```

The image proof first validates testing ports and credentials, creates the same throwaway database and cache
namespaces as browser journeys, and captures mail through temporary Mailpit. No frontend joins the testing
project or its networks. The image exposes random loopback ports only during this proof. A disposable pinned
Traefik process shares its network namespace and consumes rules directly from the candidate. Docker-returned
probe IDs are transient process identities, not new registry services, networks, volumes, or environments.
The proof observes actual backend `404` for `/api/v1/image-proof-missing/`, not the SPA shell, checks the
container's health and headers, and inspects bounded logs. Both standalone containers and the host backend
are stopped before testing resources are removed; residue checks must pass. Never run this concurrently with
browser journeys because they deliberately share throwaway testing namespaces.

## Base-image evidence, 2026-10-11

The Docker Hub registry returned Node's linux/amd64 manifest digest
`sha256:6642ef280aebc09c4541bee0b15c9f89f0f3f3c247ddee79ae1d37eddfdcbbaa` for
`node:24.20.0-bookworm-slim`. nginxinc's registry returned the multi-platform digest
`sha256:adf5042a17f4ecdd200c595fa9ffd1be37efb18f89a830bd1a00e4ab4d59d42c` for
`nginx-unprivileged:1.30.4-alpine`, published 2026-09-14. The Node build stage is intentionally linux/amd64,
matching this machine; another architecture needs its own verified manifest, never a guessed digest.
