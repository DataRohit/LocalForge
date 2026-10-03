# 69: Public runtime truth verification

**What to build:** Prove the public deployment works end to end from outside the Docker host and remains healthy,
private, observable, and reversible.

**Blocked by:**

- [68: Public runtime security hardening](68-public-runtime-security.md)

**Governing sources:**

- [Phase 9 architecture](../../docs/architecture/phase-9-public-edge-email.md)
- [Phase 9 runbook](../../docs/runbooks/phase-9-public-edge-email.md)
- [Service inventory](../../docs/platform/service-inventory.md)
- [Runtime truth rule](../../AGENTS.md)

**Status:** done

- [x] From an external network, verify DNS, TLS, canonical redirects, `/health/`, API authentication, and an
      authenticated WebSocket exchange through `localforge.datarohit.com`.
- [x] Verify development registration, activation, password recovery, and username recovery mail through Resend.
- [x] Run the complete testing suite with Mailpit and prove it remains independent of Resend.
- [x] Run project-scoped Docker health and ownership audits; inspect every affected container in a bounded post-start
      and post-exercise log window.
- [x] Treat unexplained warnings, errors, critical records, restarts, unhealthy state, public operator access, or
      leaked secrets as gate failures.

Runtime evidence captured 2026-09-25:

- DNS resolved through Cloudflare from the host resolver. Apex and `www` HTTP and HTTPS requests each returned `301`
  to `https://localforge.datarohit.com/health/`. Public HTTPS `/health/` returned `200` with HSTS.
- Public JWT creation succeeded for a temporary active probe account. An external `wss://` connection with the JWT
  as the sole subprotocol and exact public Origin completed an authenticated handshake; an invalid client frame then
  received the documented `4400` close classification. Probe account and credentials were deleted afterward.
- External registration returned `201`; activation returned `204`; password-reset and username-reset requests each
  returned `202`. Resend dashboard showed `Delivered` for activation, password-recovery, and username-recovery
  messages from `no-reply@localforge.datarohit.com` to the operator-controlled recipient.
- The source-matched testing image was rebuilt with `uv run poe testing-rebuild`. The container and host verification
  both completed `2149` collections (`2126` core plus `23` timing), with 100% core coverage and status 0. Mailpit was
  created and removed by the supported runner; no Resend credential was used by testing.
- The authenticated REST probe used the application edge at `127.0.0.1:8080` with the public Host header: `POST
  /api/v1/jwt/create/` followed by `GET /api/v1/users/me/` returned `200`; the temporary probe account was deleted
  afterward. The public edge was probed from the host through Cloudflare; DNS was checked with `Resolve-DnsName`, and
  each apex/www HTTP+HTTPS redirect was checked with `Invoke-WebRequest`.
- Runtime gates used `uv run poe development-health`, `uv run poe testing-health`, `uv run poe docker-audit`, and
  `uv run python -m scripts.audit_security --scope runtime`. `docker compose --project-name localforge-dev logs
  --since 2s --no-color` was inspected immediately after the final exercise on 2026-09-25; the affected development
  containers (`traefik-tk2jp`, `cloudflared-cf7q2`, `django-uv5n2`, workers, mail, and backing services) were clean.
  Testing post-run output recorded healthy `localforge-test` containers, ownership PASS, and clean bounded log windows;
  no warnings, errors, restarts, unhealthy state, public operator access, or credential disclosure was observed.
