# 77: Clean checkout and runtime verification

**What to build:** Prove the restructured monorepo works from a clean checkout through backend quality, Docker, SOPS,
security, and runtime truth gates.

**Blocked by:**

- [76: CI, hooks, and documentation path migration](76-ci-hooks-and-documentation-paths.md)

**Governing sources:**

- [Phase 10 specification](../../docs/architecture/phase-10-monorepo-spec.md)
- [Runtime truth rule](../../AGENTS.md)
- [Security audit](../../docs/security/security-audit.md)
- [Build plan](../../docs/build/plan.md)

**Status:** pending

- [ ] From a clean checkout, sync backend dependencies and run all static and documentation gates.
- [ ] Decrypt both committed environment files and prove parity with the generated local environment contract.
- [ ] Rebuild and exercise both Compose environments, public health, affected services, Docker ownership, and bounded
      logs.
- [ ] Prove no stale path, duplicate backend project, untracked secret, or accidental frontend file remains.
