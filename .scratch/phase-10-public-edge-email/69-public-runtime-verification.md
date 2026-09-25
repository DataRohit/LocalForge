# 69: Public runtime truth verification

**What to build:** Prove the public deployment works end to end from outside the Docker host and remains healthy,
private, observable, and reversible.

**Blocked by:** [68: Public runtime security hardening](68-public-runtime-security.md)

**Governing sources:**

- [Phase 10 architecture](../../docs/architecture/phase-10-public-edge-email.md)
- [Phase 10 runbook](../../docs/runbooks/phase-10-public-edge-email.md)
- [Service inventory](../../docs/platform/service-inventory.md)
- [Runtime truth rule](../../AGENTS.md)

**Status:** ready-for-agent

- [ ] From an external network, verify DNS, TLS, canonical redirects, `/health/`, API authentication, and an
      authenticated WebSocket exchange through `localforge.datarohit.com`.
- [ ] Verify development registration, activation, password recovery, and username recovery mail through Resend.
- [ ] Run the complete testing suite with Mailpit and prove it remains independent of Resend.
- [ ] Run project-scoped Docker health and ownership audits; inspect every affected container in a bounded post-start
      and post-exercise log window.
- [ ] Treat unexplained warnings, errors, critical records, restarts, unhealthy state, public operator access, or
      leaked secrets as gate failures.
