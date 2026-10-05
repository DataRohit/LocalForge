# 74: Backend source, tests, and scripts

**What to build:** Move backend-owned Python source, tests, and operator scripts under `backend/` while preserving
imports, test discovery, and script contracts.

**Blocked by:**

- [73: Backend Python project boundary](73-backend-python-project.md)

**Governing sources:**

- [Phase 10 architecture](../../docs/architecture/phase-10-monorepo.md)
- [Documentation standard](../../docs/platform/documentation-standard.md)
- [Agent instructions](../../AGENTS.md)

**Status:** done

- [x] Move backend source, tests, and Python operator scripts into their documented backend ownership paths.
- [x] Update import roots, Django entry points, test discovery, script subprocess paths, and coverage paths.
- [x] Preserve structured docstrings, no-comment policy, strict types, branch coverage, and parallel test execution.
- [x] Prove no duplicate backend source or script tree remains at root.

Evidence: Git moves root `src/`, `tests/`, and `scripts/` ownership into `backend/src/`, `backend/tests/`, and
`backend/scripts/`. Backend-relative Ruff, mypy, ty, pytest,
coverage, and Poe paths now resolve from the moved project; repository-root settings, operator scripts, test harnesses,
and subprocess `PYTHONPATH` values resolve through `backend/`. Root Makefile targets select the backend project and preserve
caller-independent execution. `make lint`, `make docs-standard`, `make help`, `make sync`, and `make check` pass. Root
`src/`, `tests/`, and `scripts/` trees no longer exist.
