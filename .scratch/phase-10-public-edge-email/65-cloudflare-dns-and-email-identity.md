# 65: Cloudflare DNS and email identity

**What to build:** Make Cloudflare authoritative for the public LocalForge hostname and sender identity, with root
email authentication and canonical host redirects documented and verifiable.

**Blocked by:** [64: Public deployment scope and configuration contract](64-public-deployment-scope.md)

**Governing sources:**

- [Public edge and Resend ADR](../../docs/adr/0022-public-edge-and-resend.md)
- [Phase 10 runbook](../../docs/runbooks/phase-10-public-edge-email.md)
- [Cloudflare DNS documentation](https://developers.cloudflare.com/dns/)
- [Resend domain documentation](https://resend.com/docs/dashboard/domains/introduction)

**Status:** ready-for-agent

- [ ] Verify `localforge.datarohit.com` in Resend and record the exact DNS records shown by Resend; never invent or
      merge provider records.
- [ ] Keep Resend records DNS-only and verify `no-reply@localforge.datarohit.com` as the development sender.
- [ ] Publish apex DMARC for `datarohit.com` with `p=none`, aggregate reports routed to the monitored support address,
      and a documented review date before enforcement.
- [ ] Make `datarohit.com` and `www.datarohit.com` resolve through a canonical redirect to
      `localforge.datarohit.com` without adding an application route.
- [ ] Verify DNS answers, TLS certificate coverage, redirect behaviour, and absence of accidental public records for
      operator services.
