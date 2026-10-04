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

**Status:** pending

- [ ] Update `AGENTS.md`, README files, build plan, prerequisites, docs, and ticket references for the new paths.
- [ ] Update CI, pre-commit, commit hooks, Git attributes, and all quality commands to target backend files correctly.
- [ ] State that environment files, Compose, docs, policy, and future frontend ownership remain global.
- [ ] Confirm docs contain no stale root backend paths or claims that Phase 10 created a frontend.
