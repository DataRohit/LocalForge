# 65: Cloudflare DNS and email identity

**What to build:** Make Cloudflare authoritative for the public LocalForge hostname and sender identity, with root
email authentication and canonical host redirects documented and verifiable.

**Blocked by:** [64: Public deployment scope and configuration contract](64-public-deployment-scope.md)

**Governing sources:**

- [Public edge and Resend ADR](../../docs/adr/0022-public-edge-and-resend.md)
- [Phase 10 runbook](../../docs/runbooks/phase-10-public-edge-email.md)
- [Cloudflare DNS documentation](https://developers.cloudflare.com/dns/)
- [Resend domain documentation](https://resend.com/docs/dashboard/domains/introduction)

**Status:** complete

**External evidence (2026-09-25):** Cloudflare is authoritative for `datarohit.com` and the authenticated Resend
dashboard reports `localforge.datarohit.com` as **Verified** in `ap-northeast-1` (Tokyo), with sending enabled. The
named `localforge-public` Tunnel is now provisioned by Ticket 66 and routes the canonical hostname; its
provider-managed CNAME remains the source of truth and is not duplicated here.

- [x] Verify `localforge.datarohit.com` in Resend and record the exact DNS records shown by Resend; never invent or
      merge provider records.
- [x] Keep Resend records DNS-only and verify the verified-domain sender contract for
      `no-reply@localforge.datarohit.com`.
- [x] Publish apex DMARC for `datarohit.com` with `p=none`, aggregate reports routed to the monitored support address,
      and a documented review date before enforcement.
- [x] Make `datarohit.com` and `www.datarohit.com` resolve through the defined canonical redirect to
      `localforge.datarohit.com` without adding an application route.
- [x] Verify public DNS answers from `1.1.1.1` and `8.8.8.8`, TLS and redirect behaviour for the configured apex/www
      aliases, and absence of accidental public operator records. Ticket 66 records the named Tunnel route and
      provider-managed CNAME; this ticket records the DNS ownership and redirect contract.
