# 73: Backend Python project boundary

**What to build:** Move Python project metadata and the backend virtual-environment boundary into `backend/`.

**Blocked by:**

- [72: Global environment and tooling contract](72-global-environment-and-tooling-contract.md)

**Governing sources:**

- [Phase 10 architecture](../../docs/architecture/phase-10-monorepo.md)
- [Phase 10 specification](../../docs/architecture/phase-10-monorepo-spec.md)
- [Dependency ADR](../../docs/adr/0016-accept-release-lag.md)
- [Build prerequisites](../../docs/build/prerequisites.md)

**Status:** pending

- [ ] Place `pyproject.toml`, `uv.lock`, `.python-version`, and the local `.venv` boundary under `backend/`.
- [ ] Preserve dependency groups, pinned versions, task definitions, coverage, lint, type, and test settings.
- [ ] Make root entry points invoke the backend project without relying on the caller's current directory.
- [ ] Prove a clean backend environment can sync from the moved metadata.
