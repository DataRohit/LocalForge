# 74: Backend source, tests, and scripts

**What to build:** Move backend-owned Python source, tests, and operator scripts under `backend/` while preserving
imports, test discovery, and script contracts.

**Blocked by:**

- [73: Backend Python project boundary](73-backend-python-project.md)

**Governing sources:**

- [Phase 10 architecture](../../docs/architecture/phase-10-monorepo.md)
- [Documentation standard](../../docs/platform/documentation-standard.md)
- [Agent instructions](../../AGENTS.md)

**Status:** pending

- [ ] Move backend source, tests, and Python operator scripts into their documented backend ownership paths.
- [ ] Update import roots, Django entry points, test discovery, script subprocess paths, and coverage paths.
- [ ] Preserve structured docstrings, no-comment policy, strict types, branch coverage, and parallel test execution.
- [ ] Prove no duplicate backend source or script tree remains at root.
