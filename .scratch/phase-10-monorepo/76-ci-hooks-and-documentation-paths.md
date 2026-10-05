# 76: CI, hooks, and documentation path migration

**What to build:** Reconcile CI, pre-commit, Git attributes, root instructions, docs, and ticket links with the
backend-first monorepo layout.

**Blocked by:**

- [75: Docker and Compose path migration](75-docker-and-compose-paths.md)

**Governing sources:**

- [Phase 10 architecture](../../docs/architecture/phase-10-monorepo.md)
- [Writing for agents](../../.agents/skills/writing-for-agents/SKILL.md)
- [Commit convention](../../COMMIT_CONVENTION.md)
- [Agent instructions](../../AGENTS.md)

**Status:** done

- [x] Update `AGENTS.md`, README files, build plan, prerequisites, docs, and ticket references for the new paths.
- [x] Update CI coordination, pre-commit, commit hooks, Git attributes, and all quality commands to target
  backend files correctly.
- [x] State that environment files, Compose, docs, policy, and future frontend ownership remain global.
- [x] Confirm docs contain no stale root backend paths or claims that Phase 10 created a frontend.

Evidence: The root Makefile invokes backend Poe tasks explicitly, the commit-message hook uses the backend uv project while
retaining `.github/scripts/` at root, and `.gitattributes` applies LF and Python diff handling to `backend/**`. The
backend ty task checks backend-owned trees plus the root policy scripts without invalid parent globs. Active README,
agent, build, prerequisite, platform, security, ADR, handover, and ticket references use `backend/` paths or root
Makefile targets; historical pre-move evidence carries an explicit translation note. The committed OpenAPI artifact and
source evidence map now cite `backend/tests/...`, while collection normalizes those paths at the backend project root.
No frontend files were added; environment files, Compose, documentation, and repository policy remain global.

Verification: `make pre-commit` passed. `make openapi-check` passed 9
contract tests. `make check` passed Django checks, OpenAPI, Ruff, structured documentation, mypy, ty,
architecture tests, 2,158 parallel core tests at 100% branch coverage, 23 security-timing tests, host runtime health,
Docker ownership, bounded logs, and five registration-timing stability attempts.
