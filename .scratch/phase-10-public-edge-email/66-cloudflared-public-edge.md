# 66: Cloudflare Tunnel edge

**What to build:** Route public HTTPS traffic for `localforge.datarohit.com` through one authenticated Cloudflare
Tunnel to Traefik, while keeping direct Docker ports and operator services private.

**Blocked by:**

- [64: Public deployment scope and configuration contract](64-public-deployment-scope.md)
- [65: Cloudflare DNS and email identity](65-cloudflare-dns-and-email-identity.md)

**Governing sources:**

- [Public edge and Resend ADR](../../docs/adr/0022-public-edge-and-resend.md)
- [Phase 10 runbook](../../docs/runbooks/phase-10-public-edge-email.md)
- [Cloudflare Tunnel documentation](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/)
- [Service inventory](../../docs/platform/service-inventory.md)

**Status:** ready-for-agent

- [ ] Add a pinned Cloudflared service using the Phase 10 registry name and the existing Traefik edge network.
- [ ] Store the named Tunnel credential through the approved secret workflow; no token appears in Compose, logs, or
      committed files.
- [ ] Configure one hostname ingress to Traefik and a default reject rule; route no dashboard, broker, database,
      storage, metrics, log, or Mailpit path.
- [ ] Keep direct application and dashboard ports loopback-only or Docker-internal, and prove the Tunnel is the only
      public path.
- [ ] Add startup health, restart policy, bounded logs, and credential rotation instructions.
