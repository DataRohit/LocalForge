# 70: Phase 9 handover

**What to build:** Record the complete public deployment handover so another operator can reproduce, verify, rotate,
and roll back the Cloudflare Tunnel and Resend setup.

**Blocked by:**

- [69: Public runtime truth verification](69-public-runtime-verification.md)

**Governing sources:**

- [Phase 9 architecture](../../docs/architecture/phase-9-public-edge-email.md)
- [Phase 9 runbook](../../docs/runbooks/phase-9-public-edge-email.md)
- [Phase 9 ADR](../../docs/adr/0022-public-edge-and-resend.md)
- [Build plan](../../docs/build/plan.md)
- [Ticket index](../README.md)

**Status:** pending

- [x] Record exact Cloudflare DNS, Tunnel, Resend, TLS, health, external behaviour, and bounded log evidence.
- [x] Record secret ownership, rotation dates, review dates, and the no-secret-in-repository check.
- [x] Run and record the complete container and host timing stages with 100% core branch coverage.
- [ ] Record controlled post-remediation inbox or junk placement plus complete SPF, DKIM, and DMARC results.
- [ ] Run and record the reversible rollback rehearsal for DNS, Tunnel, email transport, and application configuration.
- [x] Reconcile all documentation and governing instructions with the shipped public deployment.
- [ ] Confirm Phase 9 gate passes after the deliverability check and rollback rehearsal.

Durable evidence: [Phase 9 handover](../../docs/handover/phase-9.md).
