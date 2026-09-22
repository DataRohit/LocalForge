---
status: accepted
date: 2026-09-13
---

# Traefik as the reverse proxy

Traefik v3.7.13 (2026-09-04) fronts the stack. It patches every one to two weeks, its repository was pushed
2026-09-11, and the v2.11 line is still receiving patches — evidence of a maintenance policy rather than mere churn.
MIT licensed.

Three properties decided it for this platform specifically:

1. It discovers backends from Docker labels, so adding a service needs no proxy config file and no proxy restart.
2. It ships a native dashboard, so the reverse proxy needs no companion UI service.
3. Its router / service / middleware model is the closest conceptual match to Kubernetes Ingress, which keeps
   [../platform/kubernetes-mapping.md](../platform/kubernetes-mapping.md) honest rather than aspirational.

## Considered options

**Nginx 1.30.4** and **HAProxy 3.4.4** — both excellent and both require a static configuration file that must be
hand-edited and reloaded whenever a service is added. Neither ships a dashboard in its open build: HAProxy's stats
page is minimal and Nginx's status module is absent from the official image.

**Caddy v2.11.4** — automatic HTTPS is its headline feature and is worthless on an offline network. Its Docker
service discovery needs a third-party module, and its cadence is the slowest of the four. Caddy publishes no formal
release-cadence policy; the ~1–3 month interval is inferred from tag dates.

## Consequences

Traefik reads the Docker socket to discover containers. That is a real privilege, so it is mounted read-only and
Traefik is the only service besides the log collector permitted to see it. A read-only bind stops the file being
written; it does **not** make the API read-only, so the process stays root-equivalent over the daemon. Recorded for
the security audit rather than pretended away.

**Accepted risk, reviewed 2026-09-22; review again 2026-12-22.** Traefik and Alloy remain root-equivalent through
the Docker API, and the official Traefik image runs as root so it can read that socket. A filtering socket proxy
would add another network-reachable privileged service, another image and credential boundary, and another
availability dependency without removing the daemon authority required by container discovery. This is accepted
only for the fully local single-user platform: every host publication is loopback-only, the socket mounts are
read-only and limited to these two registered services, and the runtime secret audit rejects credentials in their
image histories or logs.

The dashboard and public web entrypoints are loopback-only. Traefik's unauthenticated `/ping` endpoint moved to a
dedicated internal `health` entrypoint on port 8082, which is not published; the authenticated dashboard remains
on port 8080 inside the container and host port 8081. An unauthenticated request to dashboard API data must return
401.

**Two defaults are kept deliberately, verified 2026-09-14.** Traefik warns at every start that it rejects some
encoded characters in request paths and suggests relaxing that when a backend is not RFC 3986 compliant. Django is,
so the strict defaults stay and the warning is expected noise rather than an action. `global.checkNewVersion`
defaults to **true** and is turned off, along with anonymous usage reporting, because this platform runs offline.

Access logging sets `addInternals: true`. Without it Traefik logs nothing for requests served by internal
services, and the dashboard router forwards to `api@internal` — so every login success and failure on the admin
surface would go unrecorded, which is the opposite of what the request log exists for.

**Forwarded client addresses stay proxy-owned.** Recorded 2026-09-17 for Ticket 35. The public entrypoint keeps
`forwardedHeaders.insecure: false`, so caller-supplied forwarding metadata is not trusted, while Traefik synthesizes
the standard forwarded address it sends to Django. The application accepts that chain only when the immediate peer
belongs to the explicit `10.89.2.0/24` edge subnet. Requests arriving through the loopback diagnostic publication
or the proxy-free testing environment use `REMOTE_ADDR`, so adding a spoofed header cannot create a new throttle
identity.
