# Phase 10 public edge and email handover

Handover recorded **2026-09-26** after Tickets 64–69. The existing `localforge-dev` Compose deployment is the public
development deployment; testing remains the separate `localforge-test` Mailpit-backed environment.

## Public deployment record

| Item | Recorded value or owner |
| --- | --- |
| Canonical hostname | `https://localforge.datarohit.com` |
| Public application route | Cloudflare named Tunnel `localforge-public` → `http://traefik-tk2jp:80` |
| Tunnel service | `cloudflared-cf7q2`, pinned to Cloudflare `2026.9.3`, readiness on internal port `2000` |
| Cloudflare DNS | Cloudflare authoritative; provider-managed Tunnel target; apex and `www` are originless redirect hosts using `192.0.2.0` |
| Resend sender | `LocalForge <no-reply@localforge.datarohit.com>` |
| Resend verification | Verified `2026-09-25`; DKIM/SPF records remain DNS-only |
| DMARC | `_dmarc.datarohit.com`, `p=none`, aggregate review **2026-10-25** |
| Testing sender | `no-reply@localforge.invalid` through Mailpit |

The exact Resend provider rows are preserved in the [Phase 10 runbook](../runbooks/phase-10-public-edge-email.md).
The Tunnel CNAME target is intentionally provider-managed and is not duplicated here; Ticket 66 records the named
route and provider ownership. No Tunnel token or Resend key is copied into this report.

## Verification evidence

- `Resolve-DnsName localforge.datarohit.com` returned Cloudflare A/AAAA addresses. Apex and `www` HTTP and HTTPS
  requests returned permanent redirects to `https://localforge.datarohit.com/health/`.
- Public `/health/` returned `200` with HSTS. Public schema and documentation routes returned `200`; admin, dashboards,
  Mailpit, metrics, Grafana, Flower, and pgAdmin returned `404`.
- A temporary active account proved JWT issuance and an authenticated external `wss://` handshake with the exact
  public Origin; an invalid frame received the documented `4400` close. A protected REST probe through the Traefik
  edge returned `200` for `/api/v1/users/me/`. Temporary accounts and credentials were deleted.
- Resend showed Delivered activation, password-recovery, and username-recovery messages from the configured sender.
- `uv run poe development-health`, `uv run poe testing-health`, `uv run poe docker-audit`, and
  `uv run python -m scripts.audit_security --scope runtime` passed. A bounded `docker compose --project-name
  localforge-dev logs --since 2s --no-color` window after the final exercise was clean; affected containers had no
  unexplained warning-or-higher records, restarts, unhealthy state, public operator access, or credential disclosure.
- Targeted verification after a source-matched `uv run poe testing-rebuild` passed the security test module (6 tests),
  and the rebuilt container and host core suites each passed 2,126 tests with 100% coverage. The long timing tail is
  intentionally deferred for the operator's manual full-suite run.

## Secret ownership and rotation

| Secret | Owner | Storage | Rotation or review |
| --- | --- | --- | --- |
| `TUNNEL_TOKEN` | Cloudflare Zero Trust owner | generated development SOPS env, `env_file` only | Baseline rotation **2026-09-25**; review **2026-10-25**. Disable route, revoke old token, replace secret, recreate `cloudflared-cf7q2`, then check readiness |
| `RESEND_API_KEY` | Resend/domain owner | generated development SOPS env, `env_file` only | Baseline rotation **2026-09-25**; review **2026-10-25**. Create replacement domain-scoped key, replace secret, recreate Django/workers, verify delivery, then revoke old key |
| SOPS age key | LocalForge operator | operator secret store | Baseline review **2026-10-25**; required to decrypt or regenerate environment files and never committed |

The repository check is clean: `git grep` and the active `detect-private-key` hook found no provider key, Tunnel token,
or plaintext secret in tracked files or bounded logs. If exposure is suspected, revoke immediately, replace the secret,
recreate affected services, and inspect delivery and logs.

## Rollback and recovery

1. Disable the Cloudflare Tunnel hostname route.
2. Remove or disable apex/`www` Redirect Rules and restore the prior DNS records from the Cloudflare audit log.
3. Restore development mail transport to Mailpit and recreate Django/worker services.
4. Revoke suspected Tunnel or Resend credentials, replace them through the generated-secret workflow, and verify health,
   delivery, and clean logs before re-enabling public ingress.

The rollback sequence is reversible. The supported application-side commands are `uv run poe secrets-generate`,
`uv run poe development-rebuild`, and `uv run poe development-health`; Cloudflare DNS, Redirect Rules, Tunnel route,
and Resend revocation are provider-console actions recorded in the runbook. A live rollback rehearsal was not run:
no DNS, credential, or data-volume mutation was authorized for this evidence-only handover. The authoritative
step-by-step procedure remains the [Phase 10 runbook](../runbooks/phase-10-public-edge-email.md).

## Phase gate and stop condition

The public edge, email transport, private operator boundary, secret handling, health checks, and bounded runtime
observations are recorded above and in Tickets 64–69. The implementation handover is prepared; final gate closure
still requires the operator's manual full timing suite and rollback rehearsal. Stop here until those checks or a new
governing document and ticket set authorizes further scope.
