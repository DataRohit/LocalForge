# 75: Docker and Compose path migration

**What to build:** Update Docker build contexts, entrypoints, Compose mounts, and service commands for the backend
subtree while keeping orchestration global.

**Blocked by:**

- [74: Backend source, tests, and scripts](74-backend-source-tests-and-scripts.md)

**Governing sources:**

- [Phase 10 architecture](../../docs/architecture/phase-10-monorepo.md)
- [Service inventory](../../docs/platform/service-inventory.md)
- [Build plan](../../docs/build/plan.md)
- [Docker conventions](../../docs/platform/conventions.md)

**Status:** done

- [x] Keep Compose files and registered service, network, volume, and environment names unchanged.
- [x] Point backend image contexts, copied files, entrypoints, health checks, and test stages at `backend/`.
- [x] Preserve offline boundaries, pinned images, generated secrets, and both existing Compose projects.
- [x] Rebuild and health-check development and testing environments after path changes.

Evidence: Dockerfiles copy backend-owned source, scripts, metadata, tests, and the pgBackRest entrypoint from
`backend/`; Compose keeps root orchestration and updates the replica bootstrap mount to `backend/scripts/`. The
development and testing projects rebuilt successfully, reported healthy, and passed the project-scoped Docker audit.
Container tests passed 2,158 core plus 23 security-timing tests with 100% branch coverage; host tests passed the same
suite and the five-attempt registration timing stability gate. `GET /health/` returned 200 with every dependency
working. Post-exercise logs for both projects were clean from 2026-10-04T16:34:56Z through 16:35:44Z.
