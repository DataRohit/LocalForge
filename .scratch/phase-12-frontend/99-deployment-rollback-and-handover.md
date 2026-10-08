# 99: Deployment, rollback, and handover

**What to build:** The exact procedures to deploy the frontend into `development` and roll it back, plus the
Phase 12 handover for Gate 4.

**Blocked by:**

- [98: Frontend security and supply-chain audit](98-frontend-security-and-supply-chain-audit.md)

**Governing sources:**

- [Frontend hosting and edge routing ADR](../../docs/adr/0027-frontend-hosting-and-edge-routing.md)
- [Full-stack pivot ADR](../../docs/adr/0024-full-stack-pivot.md)
- [Build plan](../../docs/build/plan.md)
- [Ticket index](../README.md)

**Status:** pending

- [ ] A runbook lists every command to deploy the candidate files, the health and behavior checks to run, and the
      bounded log window to inspect, each marked as requiring the user's authorization.
- [ ] A rollback procedure restores the previous routing with no data loss and is rehearsed against `testing` or a
      standalone project.
- [ ] The handover records final ownership, commands, evidence, and the unverified production gates.
- [ ] Nothing has been applied to `localforge-dev`.
- [ ] Gate 4 report is delivered; deployment waits for the user's explicit authorization.
