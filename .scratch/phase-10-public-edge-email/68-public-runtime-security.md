# 68: Public runtime security hardening

**What to build:** Make the public deployment enforce the canonical HTTPS host and protect every operator boundary
while preserving the fixed application surface.

**Blocked by:**

- [66: Cloudflare Tunnel edge](66-cloudflared-public-edge.md)
- [67: Resend development transport](67-resend-development-transport.md)

**Governing sources:**

- [Root instructions](../../AGENTS.md)
- [API error contract](../../docs/adr/0018-api-error-contract.md)
- [WebSocket authentication](../../docs/adr/0019-websocket-authentication.md)
- [Access zone decision](../../docs/adr/0021-access-zone-for-published-ports.md)
- [Phase 10 architecture](../../docs/architecture/phase-10-public-edge-email.md)

**Status:** ready-for-agent

- [ ] Enforce `https://localforge.datarohit.com` in host allowlists, CSRF trusted origins, CORS origins, absolute
      account links, and authenticated WebSocket origin checks.
- [ ] Ensure proxy headers and client-address handling trust only the known Traefik-to-application edge.
- [ ] Keep Swagger/ReDoc and health behaviour explicit for the public deployment; expose no credentials, internal
      topology, or operator route through schema or error responses.
- [ ] Prove operator dashboards, Mailpit, databases, brokers, object storage, metrics, and logs remain unreachable
      through the public hostname and direct external paths.
- [ ] Add secret rotation and incident response checks for Tunnel and Resend credentials.
