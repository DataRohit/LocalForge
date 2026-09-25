---
status: accepted
date: 2026-09-25
---

# Public development edge and transactional email

The existing Docker development stack becomes the public LocalForge deployment through one Cloudflare Tunnel. The
Tunnel exposes only `localforge.datarohit.com` to Traefik; operator dashboards and direct container ports stay private.
Development sends transactional mail through the verified Resend domain `localforge.datarohit.com` using
`no-reply@localforge.datarohit.com`; testing keeps Mailpit. This adds a public deployment without creating a third
Compose environment.

## DNS and email

Cloudflare remains authoritative for `datarohit.com`. The apex receives a DMARC policy beginning at `p=none`; Resend
verification records remain DNS-only. Cloudflare Email Routing continues handling human support mail separately.

## Consequences

- `DJANGO_SITE_URL`, allowed hosts, CSRF origins, CORS origins, and WebSocket origin checks must use
  `https://localforge.datarohit.com` in the public deployment.
- The Tunnel credential and Resend sending key are generated secrets. They never enter Git, images, Compose literals,
  browser logs, or documentation.
- Public runtime verification must prove DNS, TLS, tunnel routing, health, WebSocket authentication, transactional
  delivery, private operator surfaces, and bounded clean logs.
- The old local-only and no-hosted-service statements require reconciliation before implementation begins.

## Considered options

**Expose Docker ports directly.** Rejected: bypasses the edge policy and publishes operator services accidentally.

**Create a separate production Compose environment.** Rejected for this phase: user selected the existing Docker stack
as the public deployment; a separate environment would be a later migration.

**Use the root domain as Resend's sending domain.** Rejected: keeping Resend records on the dedicated LocalForge
subdomain avoids coupling inbound Cloudflare Email Routing with outbound sender policy.
