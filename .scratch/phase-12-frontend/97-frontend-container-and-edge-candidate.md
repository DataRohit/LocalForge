# 97: Frontend container and edge candidate

**What to build:** The pinned frontend image and the inert candidate Compose and Traefik changes that would serve it
through the existing edge, ready for deployment on the user's authorization.

**Blocked by:**

- [95: Frontend unit and integration tests](95-frontend-unit-and-integration-tests.md)

**Governing sources:**

- [Frontend hosting and edge routing ADR](../../docs/adr/0027-frontend-hosting-and-edge-routing.md)
- [Conventions: the ID registry](../../docs/platform/conventions.md)
- [Service inventory](../../docs/platform/service-inventory.md)
- [Phase 12 replan record](../../docs/architecture/phase-12-frontend.md)
- [Ticket index](../README.md)

**Status:** Production-Ready image and inert candidate; development deployment unverified

## Verification record, 2026-10-11

`make frontend-image` built the digest-pinned multi-stage image with pnpm 11.21.0 through Corepack,
`pnpm install --frozen-lockfile --ignore-scripts`, and a non-root nginx runtime without package managers.
The final image's entrypoint encodes native error records as JSON, preserving severity and escaping quotes
and backslashes; nginx remains PID 1 and the filesystem is read-only except its bounded `/tmp` tmpfs.

`make frontend-image-check` passed against the actual image and pinned standalone Traefik using the
candidate's real routing expressions. It validated health, UID/GID 101, read-only root, absence of Node,
npm, pnpm, and apk, inherited security headers, one-year immutable assets, `no-cache` documents, fallback,
and backend `404` rather than SPA output for an unknown API path. A notice-level formatter fixture proved
structured error output and exact preservation of quotes and backslashes without injecting a warning.
The final runtime window starts `2026-10-11T17:19:01.011Z`; both standalone log windows and all testing
container/backend windows were clean, and every throwaway resource returned to baseline.

The image and proxy ran outside both Compose projects, with no new network or volume. The six-service
testing headless set is unchanged. The existing Alloy discovery follows the development project label
without a service allowlist, so both JSON log streams will be collected after deployment. No development
container, network, volume, or mounted collector configuration was changed. The user's renewed authorization
covers later deployment, but the candidates remain inert at this ticket boundary.

`git apply --check docs/runbooks/frontend-deployment/registry.candidate.patch` and candidate Compose
`config --quiet` pass. The registry, service inventory, Kubernetes mapping, candidate, and deferred naming
and operator registry patch agree. The patch is deliberately not applied before deployment.

Main-thread review found nginx's asset header inheritance needed an explicit merge, native error records
needed JSON encoding, and the HTTP client discarded a custom Host header. These are fixed and proven against
the actual containers; the first proxy-readiness failure cleaned up without residue. Severity checking now
reads JSON levels rather than matching ordinary words in successful log messages, with focused tests.
The convention audit correctly rejected temporary Mailpit when run during the proof; it is rerun after
cleanup, never relaxed. Final frontend static, unit, bundle, and formatting gates are rerun before closure.

Closing gates: `make frontend-check` passed 1,095 tests in 50 files, retaining 100% application coverage;
formatting, ESLint, strict types, generated-contract drift, TSDoc, build, and bundle budgets all passed.
`make testing-health` and `make convention-audit-testing` passed after proof cleanup. No unresolved
main-thread standards or specification review findings remain. Production-Ready here describes only
the image and inert candidate; the deployed platform is not yet claimed Production-Ready or deployed.

- [x] The Dockerfile uses digest-pinned base images, enables the pinned pnpm through Corepack, installs with
      `pnpm install --frozen-lockfile`, and its final stage is non-root, read-only, and has no package manager.
- [x] The image is built and tested standalone outside both Compose projects; no `localforge-dev` object is touched.
- [x] A candidate overlay and Traefik labels implement the routing table in the ADR, and are not applied to
      `compose.development.yaml`.
- [x] `/healthz`, the security headers, the immutable-asset and `no-cache` rules, and the SPA fallback are tested
      against the running image, and the Traefik service health check in the candidate uses `/healthz`.
- [x] An unknown `/api/` path in the candidate routing still reaches Django, never the SPA shell.
- [x] Collection of the frontend container's logs by the existing pipeline is confirmed, or a candidate patch is
      supplied; mounted collector configuration is not read without the user's authorization.
- [x] The registry, service inventory, Kubernetes mapping, and convention audit rows for `frontend-fe6vn` agree.
- [x] Status recorded as Production-Ready only if every item above passes; otherwise Implemented.
