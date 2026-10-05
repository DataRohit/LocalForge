# 73: Backend Python project boundary

**What to build:** Move Python project metadata and the backend virtual-environment boundary into `backend/`.

**Blocked by:**

- [72: Global environment and tooling contract](72-global-environment-and-tooling-contract.md)

**Governing sources:**

- [Phase 10 architecture](../../docs/architecture/phase-10-monorepo.md)
- [Phase 10 specification](../../docs/architecture/phase-10-monorepo-spec.md)
- [Dependency ADR](../../docs/adr/0016-accept-release-lag.md)
- [Build prerequisites](../../docs/build/prerequisites.md)

**Status:** done

- [x] Place `backend/pyproject.toml`, `backend/uv.lock`, and `backend/.python-version` under `backend/`; keep the local
  `.venv` boundary there.
- [x] Preserve dependency groups, pinned versions, task definitions, coverage, lint, type, and test settings.
- [x] Make root entry points invoke the backend project without relying on the caller's current directory.
- [x] Prove a clean backend environment can sync from the moved metadata.

Evidence (pre-move action, now complete): Git moved `pyproject.toml`, `uv.lock`, and `.python-version` into `backend/`;
uv created `backend/.venv`
and installed the locked 123-package environment with `uv sync --project backend --locked
--no-install-project`. `uv lock --check --project backend` passed. Backend-relative configuration keeps the existing
root source trees usable during the staged move. Root Makefile targets select `backend/`, export the transitional
repository import paths, and resolve the repository from the script location. `make help`, `make lint`, and
`make docs-standard` passed from `docs/`; `make preflight` passed
all 14 checks, including `backend/.venv` and GNU Make.
