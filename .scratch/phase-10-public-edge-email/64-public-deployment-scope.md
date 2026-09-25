# 64: Public deployment scope and configuration contract

**What to build:** Establish the documented public deployment contract so the existing Docker stack can serve
`localforge.datarohit.com` without creating a third Compose environment or exposing operator services.

**Blocked by:** [63: Phase 8 handover](../phase-8-solid-architecture/63-phase-8-handover.md)

**Governing sources:**

- [Root instructions](../../AGENTS.md)
- [Build plan](../../docs/build/plan.md)
- [Phase 10 architecture](../../docs/architecture/phase-10-public-edge-email.md)
- [Public edge and Resend ADR](../../docs/adr/0022-public-edge-and-resend.md)
- [Platform conventions](../../docs/platform/conventions.md)
- [Documentation standard](../../docs/platform/documentation-standard.md)

**Status:** ready-for-agent

- [ ] Reconcile the glossary and governing instructions with the accepted public deployment: development remains the
      existing Compose project, testing remains Mailpit-backed, and no separate production Compose environment exists.
- [ ] Add the Cloudflared service, secret, network, and environment entries to the frozen registry before any Compose
      mutation uses them.
- [ ] Define public host, origin, CSRF, CORS, WebSocket, email, and documentation settings without hardcoded secrets.
- [ ] Define rollback, credential rotation, DNS ownership, and external dependency boundaries.
- [ ] Update the phase and ticket indexes so Phase 10 is discoverable from the canonical paths.
