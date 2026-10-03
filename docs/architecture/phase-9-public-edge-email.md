# Phase 9: Public edge and transactional email

Phase 9 turns the verified LocalForge Docker stack into a controlled public deployment. Cloudflare Tunnel carries
HTTPS traffic for `localforge.datarohit.com` to the existing Traefik edge. Resend sends development transactional mail
from `no-reply@localforge.datarohit.com`; Mailpit remains the testing transport.

The public deployment is the existing `development` Compose project (`localforge-dev`). It is not a production
environment and does not add a second application stack. The Cloudflare client is the only new development service,
uses `cloudflared-cf7q2`, joins `edge-net-ne2vk`, and can reach only Traefik's web entrypoint.

## Scope

- Cloudflare Tunnel with one ingress route to Traefik.
- Cloudflare DNS and TLS for `localforge.datarohit.com`.
- Apex DMARC policy with an observation-first rollout and review on **2026-10-25**.
- Resend sending-domain verification and domain-scoped sending credentials.
- Development email transport selection, with Mailpit unchanged for testing.
- Public host, origin, CSRF, CORS, WebSocket, and documentation security settings.
- Runtime truth evidence and rollback runbook.

## Security boundary

Public ingress reaches only the application router. Traefik, Grafana, Prometheus, Loki, Flower, pgAdmin, Mailpit,
PostgreSQL, Valkey, RabbitMQ, SeaweedFS, and direct Django ports remain loopback-only or Docker-internal. Tunnel
ingress rejects unknown hostnames and routes no path to an operator service.

## Email contract

Development sender: `LocalForge <no-reply@localforge.datarohit.com>`.

Testing sender: existing Mailpit-backed `no-reply@localforge.invalid`.

Support replies use `support@datarohit.com` through the existing Cloudflare Email Routing path. No mailbox or
forwarding rule is required for the no-reply sender.

DNS ownership stays with Cloudflare. Resend verified `localforge.datarohit.com` on 2026-09-25; its captured DKIM and
SPF CNAME records remain DNS-only. The apex DMARC record is `_dmarc.datarohit.com TXT "v=DMARC1; p=none;
rua=mailto:datarohit@outlook.com"`; enforcement waits for the dated review. Apex and `www` use proxied originless
records only as Redirect Rule hosts, and never point to a LocalForge origin or application route. Ticket 66 completed
the application route as `localforge.datarohit.com` through the named `localforge-public` Tunnel; Cloudflare manages the
provider CNAME target.

## Phase gate

The gate passes only when DNS and TLS resolve from an external network, the Tunnel reaches `/health/`, authenticated
HTTP and WebSocket flows work, development email arrives at a controlled recipient through Resend, testing still
captures mail in Mailpit, all operator surfaces remain private, secrets are absent from repository and logs, and every
affected container has a clean bounded observation window.
