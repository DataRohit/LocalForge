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
- [Phase 9 architecture](../../docs/architecture/phase-9-public-edge-email.md)

**Status:** done

- [x] Enforce `https://localforge.datarohit.com` in host allowlists, CSRF trusted origins, CORS origins, absolute
      account links, and authenticated WebSocket origin checks.
- [x] Ensure proxy headers and client-address handling trust only the known Traefik-to-application edge. The public
      router sets `X-Forwarded-Proto=https`; Django trusts that explicit edge contract, while forwarded client
      addresses remain restricted to `10.89.2.0/24`.
- [x] Keep Swagger/ReDoc and health behaviour explicit for the public deployment; expose no credentials, internal
      topology, or operator route through schema or error responses.
- [x] Prove operator dashboards, Mailpit, databases, brokers, object storage, metrics, and logs remain unreachable
      through the public hostname and direct external paths.
- [x] Add secret rotation and incident response checks for Tunnel and Resend credentials.

Runtime evidence captured 2026-09-25:

- `uv run python -m scripts.manage_platform development-health` passed after rebuilding and recreating Django.
- `uv run python -m scripts.manage_platform docker-audit` passed.
- `uv run python -m scripts.audit_security --scope runtime` passed; protected operator endpoints returned expected
  denial responses and private host ports stayed closed.
- Public HTTPS returned `200` for `/health/`, `/api/schema/`, Swagger UI, and ReDoc, each with
  `Strict-Transport-Security: max-age=31536000`.
- Public HTTPS returned `404` for `/admin/`, dashboards, Mailpit, metrics, Grafana, Flower, and pgAdmin. Direct
  operator ports remained loopback-only.
- A bounded two-second post-exercise development log window was clean. The runbook records normal Tunnel and Resend
  rotation, revocation, rollback, delivery verification, and suspected-exposure response checks.
- Unit coverage proves forwarded HTTPS is accepted from `10.89.2.0/24` and stripped from direct loopback peers;
  local HTTP admin responses keep non-secure cookies while public HTTPS responses mark cookies secure.
