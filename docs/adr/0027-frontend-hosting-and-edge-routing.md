---
status: accepted
date: 2026-10-08
---

# Frontend hosting and edge routing

The built SPA is served by one static `nginx-unprivileged` container, `frontend-fe6vn`, behind the existing Traefik
and Cloudflare Tunnel path. One hostname serves everything, so the browser sees a single origin. Traefik sends API,
WebSocket, health, admin, and static routes to Django and every other path to the frontend.

## Routing contract

| Path | Target |
| --- | --- |
| `/api/`, `/ws/`, `/health/`, `/admin/`, `/static/` | `django-uv5n2` (public `/admin` stays blocked) |
| every other path | `frontend-fe6vn`, falling back to `index.html` for client routes |

Django wins any prefix it owns, so an unknown `/api/...` path still returns the Django error envelope rather than
the SPA shell. The SPA's own build assets live under `/assets/`, which Django does not own.

## The container

- Built from `docker/frontend/Dockerfile`: a digest-pinned Node build stage, then a digest-pinned
  `nginx-unprivileged` runtime stage with no package manager, running as non-root with a read-only filesystem.
- Joins `edge-net-ne2vk` only; no volume, no secret, no published host port.
- Health check on `/healthz`, an nginx location answered without touching files; Traefik's service health check
  for the frontend uses the same path, as the Django service does for `/health/`.
- Hashed files under `/assets/` are cached as immutable for a year; `index.html` is `no-cache`.
- The SPA document carries its own `Content-Security-Policy`, `Referrer-Policy: no-referrer`, and security headers;
  Django keeps setting headers for API responses.
- nginx writes structured logs to stdout for the existing log pipeline. Ticket 97 confirms collection; the
  collector's mounted configuration is not read without the user's authorization.
- Registered in the conventions registry and the service inventory before any Compose edit.

## Deployment gate

Compose and Traefik label changes that reach `localforge-dev` are authored as inert candidate files and applied only
after the user authorizes deployment. They are never merged into `compose.development.yaml` earlier.

## Considered options

**Django serves the SPA through WhiteNoise.** Rejected: it couples frontend releases to backend image rebuilds.

**A separate frontend origin.** Rejected: it forces credentialed CORS and weakens `SameSite=Strict`
([0025](./0025-browser-authentication.md)).

**Cloudflare Pages or any hosted static host.** Rejected: external hosted dependency outside the approved
boundaries in [0022](./0022-public-edge-and-resend.md).

**Traefik serving files directly.** Rejected: Traefik is a router and has no static file server.

## Consequences

- `testing` gets no frontend container; browser end-to-end tests run host processes against the `testing` backend
  (Ticket 96), keeping the headless set unchanged.
- A frontend outage cannot take down `/api/` or `/health/` because routing is by prefix.
