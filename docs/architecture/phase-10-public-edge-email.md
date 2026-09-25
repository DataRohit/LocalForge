# Phase 10: Public edge and transactional email

Phase 10 turns the verified LocalForge Docker stack into a controlled public deployment. Cloudflare Tunnel carries
HTTPS traffic for `localforge.datarohit.com` to the existing Traefik edge. Resend sends development transactional mail
from `no-reply@localforge.datarohit.com`; Mailpit remains the testing transport.

## Scope

- Cloudflare Tunnel with one ingress route to Traefik.
- Cloudflare DNS and TLS for `localforge.datarohit.com`.
- Apex DMARC policy with an observation-first rollout.
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

Support replies use the existing Cloudflare-routed support address. No mailbox or forwarding rule is required for the
no-reply sender.

## Phase gate

The gate passes only when DNS and TLS resolve from an external network, the Tunnel reaches `/health/`, authenticated
HTTP and WebSocket flows work, development email arrives at a controlled recipient through Resend, testing still
captures mail in Mailpit, all operator surfaces remain private, secrets are absent from repository and logs, and every
affected container has a clean bounded observation window.
