# 71: Monorepo boundary and inventory

**What to build:** Establish the authoritative Phase 10 ownership map and migration inventory before moving files.

**Blocked by:**

- [70: Phase 9 handover](../phase-9-public-edge-email/70-phase-9-handover.md)

**Governing sources:**

- [Phase 10 architecture](../../docs/architecture/phase-10-monorepo.md)
- [Phase 10 specification](../../docs/architecture/phase-10-monorepo-spec.md)
- [Monorepo ADR](../../docs/adr/0023-monorepo-layout.md)
- [Agent instructions](../../AGENTS.md)

**Status:** pending

- [ ] Inventory every root file and classify it as global, backend-owned, or reserved future frontend.
- [ ] Record every path-sensitive command, Docker context, CI job, hook, import, and documentation link.
- [ ] Resolve ownership disagreements before any move and commit the approved map in the Phase 10 documents.
- [ ] Confirm no frontend implementation is included.
