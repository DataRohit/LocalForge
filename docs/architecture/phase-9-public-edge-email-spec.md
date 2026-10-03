# Phase 9 public edge and email specification

## Problem Statement

LocalForge currently runs as a local Docker platform. Development email is captured by Mailpit, the application has no
public hostname, and Cloudflare's DNS recommendations are unresolved. The owner wants the existing Docker deployment
reachable at `localforge.datarohit.com` through Cloudflare Tunnel and development account mail sent through Resend.

## Solution

Use one named Cloudflare Tunnel to carry HTTPS traffic for `localforge.datarohit.com` to Traefik. Keep operator
services private. Verify `localforge.datarohit.com` in Resend and send development mail from
`no-reply@localforge.datarohit.com`; retain Mailpit for testing. Add observation-first DMARC on `datarohit.com`,
canonical apex and `www` redirects, secret rotation, and external runtime verification.

## User Stories

1. As an API consumer, I want to reach LocalForge at one HTTPS hostname, so that clients do not depend on local ports.
2. As an API consumer, I want health, REST, and authenticated WebSocket traffic to share one canonical origin, so that
   browser origin and token policies remain predictable.
3. As an operator, I want the Tunnel to expose only Traefik, so that dashboards and data services stay private.
4. As an operator, I want Tunnel credentials outside Git and images, so that a repository leak does not publish the host.
5. As a LocalForge user, I want activation and recovery mail sent from the verified project domain, so that messages
   have a stable sender identity.
6. As an operator, I want testing mail captured by Mailpit, so that tests stay deterministic and offline.
7. As a security operator, I want DMARC aggregate reporting, so that spoofing and alignment failures are visible before
   enforcement.
8. As an operator, I want apex and `www` requests redirected to the canonical hostname, so that DNS recommendations
   do not leave dead public names.
9. As an operator, I want a reproducible rollback, so that DNS, Tunnel, and Resend failures do not strand the stack.

## Implementation Decisions

- The existing development Compose project is the public deployment. No third Compose environment is introduced.
- Cloudflare Tunnel is the only public ingress. Traefik remains the application edge.
- Resend uses the verified domain `localforge.datarohit.com` and sender `no-reply@localforge.datarohit.com`.
- The Resend API key is domain-scoped and stored as a generated secret.
- Mailpit remains the testing transport and testing requires no Resend secret.
- Cloudflare Email Routing continues handling monitored support mail. The no-reply address has no mailbox or forwarding.
- Apex DMARC starts with `p=none` and a dated review before enforcement.
- Apex and `www` use Cloudflare redirects to the canonical LocalForge hostname.

## Testing Decisions

- Unit tests cover configuration selection and secret validation without contacting Resend or Cloudflare.
- Integration tests cover development transport selection, testing Mailpit selection, asynchronous delivery, and failure
  handling through existing email seams.
- Runtime checks cover external DNS, TLS, canonical redirects, health, authenticated REST, authenticated WebSocket,
  Resend delivery, Mailpit testing, private operator paths, Docker ownership, and bounded logs.
- Tests never assert provider internals. They assert application-visible delivery and routing behaviour.

## Out of Scope

- A separate production Compose environment.
- A public Cloudflare Pages or frontend application.
- Inbound receiving on the Resend sending domain.
- Public dashboards, Mailpit, databases, brokers, object storage, metrics, or log viewers.
- Marketing mail, bulk sending, or a mailbox for the no-reply sender.

## Further Notes

Implementation must reconcile the former local-only and no-hosted-service statements before runtime changes land. The
Cloudflare popup's DMARC, apex, and `www` recommendations are Phase 9 acceptance concerns, not optional cleanup.
