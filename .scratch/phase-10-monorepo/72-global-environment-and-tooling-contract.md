# 72: Global environment and tooling contract

**What to build:** Clean and document global environment, secret, Compose, and root command ownership for the
backend-first monorepo.

**Blocked by:**

- [71: Monorepo boundary and inventory](71-monorepo-boundary-and-inventory.md)

**Governing sources:**

- [Phase 10 architecture](../../docs/architecture/phase-10-monorepo.md)
- [Phase 10 inventory](../../docs/architecture/phase-10-monorepo-inventory.md)
- [Monorepo ADR](../../docs/adr/0023-monorepo-layout.md)
- [Secrets ADR](../../docs/adr/0014-sops-age-secrets.md)
- [Platform conventions](../../docs/platform/conventions.md)

**Status:** done

- [x] Keep root `.env.example`, encrypted environment files, and `.sops.yaml` as the only committed environment
      artifacts.
- [x] Remove duplicate, stale, or backend-local environment assumptions and document the root path contract.
- [x] Define root command entry points that work from any directory and delegate backend commands explicitly.
- [x] Preserve both existing environments, secret names, and SOPS decrypt/encrypt workflows.

Evidence: root environment ownership remains limited to `.env.example`, `.env.development.sops`,
`.env.testing.sops`, and `.sops.yaml`. `localforge.ps1` and `localforge.sh` select the current project root or future
`backend/` project from any caller directory. The inventory and ADR record the path and secret contract.
Ignore rules anchor encrypted-file exceptions at repository root, so nested backend environment files remain ignored.
