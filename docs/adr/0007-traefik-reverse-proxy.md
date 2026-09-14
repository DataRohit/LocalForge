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

**Two defaults are kept deliberately, verified 2026-09-14.** Traefik warns at every start that it rejects some
encoded characters in request paths and suggests relaxing that when a backend is not RFC 3986 compliant. Django is,
so the strict defaults stay and the warning is expected noise rather than an action. `global.checkNewVersion`
defaults to **true** and is turned off, along with anonymous usage reporting, because this platform runs offline.

Access logging sets `addInternals: true`. Without it Traefik logs nothing for requests served by internal
services, and the dashboard router forwards to `api@internal` — so every login success and failure on the admin
surface would go unrecorded, which is the opposite of what the request log exists for.
