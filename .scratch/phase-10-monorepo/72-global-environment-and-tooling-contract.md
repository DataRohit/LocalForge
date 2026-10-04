# 72: Global environment and tooling contract

**What to build:** Clean and document global environment, secret, Compose, and root command ownership for the
backend-first monorepo.

**Blocked by:**

- [71: Monorepo boundary and inventory](71-monorepo-boundary-and-inventory.md)

**Governing sources:**

- [Phase 10 architecture](../../docs/architecture/phase-10-monorepo.md)
- [Monorepo ADR](../../docs/adr/0023-monorepo-layout.md)
- [Secrets ADR](../../docs/adr/0014-sops-age-secrets.md)
- [Platform conventions](../../docs/platform/conventions.md)

**Status:** pending

- [ ] Keep root `.env.example`, encrypted environment files, and `.sops.yaml` as the only committed environment
      artifacts.
- [ ] Remove duplicate, stale, or backend-local environment assumptions and document the root path contract.
- [ ] Define root command entry points that work from any directory and delegate backend commands explicitly.
- [ ] Preserve both existing environments, secret names, and SOPS decrypt/encrypt workflows.
