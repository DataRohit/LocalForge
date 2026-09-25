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

**Status:** complete

- [x] Add the pinned `docker.io/cloudflare/cloudflared:2026.9.3` service `cloudflared-cf7q2` on `edge-net-ne2vk`.
- [x] Store the provider credential as native `TUNNEL_TOKEN` in the generated development env file. The earlier
      diagnostic alias was removed, the provider token was rotated, and the replacement connector log contains no
      token value.
- [x] Configure the single published route `localforge.datarohit.com` to `http://traefik-tk2jp:80` with the
      Cloudflare catch-all `http_status:404`; no operator, data, metrics, logging, or Mailpit route is published.
- [x] Prove the public path: external HTTPS `/health/` returned `200`, an unknown application path returned `404`,
      and `docker port` showed only loopback bindings for Traefik and Django; cloudflared published no host port.
- [x] Verify `development-health` and `docker-audit` passed, the Tunnel dashboard reported Healthy, and a final
      bounded 15-second development log window was clean. The runbook documents restart, rotation, and rollback.

Runtime evidence captured 2026-09-25 after the token rotation. The supported `scripts.manage_platform` commands were
invoked directly because the local Poe executable was blocked by Windows Application Control; the Poe tasks target the
same commands.
