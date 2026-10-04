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

**Status:** pending

- [ ] Keep Compose files and registered service, network, volume, and environment names unchanged.
- [ ] Point backend image contexts, copied files, entrypoints, health checks, and test stages at `backend/`.
- [ ] Preserve offline boundaries, pinned images, generated secrets, and both existing Compose projects.
- [ ] Rebuild and health-check development and testing environments after path changes.
