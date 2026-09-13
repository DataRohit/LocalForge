# 10: Edge reverse proxy with a secured dashboard

**What to build:** a reverse proxy that discovers application containers automatically and routes traffic to them,
with a dashboard a developer can log into to see the live routing table.

**Blocked by:** 03.

**Status:** ready-for-agent

- [ ] The proxy runs with the registry name and discovers backends from container labels, so adding a service needs
      no proxy config change and no restart.
- [ ] Container discovery is opt-in: services are not exposed unless they ask to be.
- [ ] The Docker socket is mounted read-only.
- [ ] The dashboard listens on its own entrypoint, separate from the traffic entrypoint.
- [ ] The dashboard router matches both the dashboard path and the API path, because the dashboard is a
      single-page app that calls the API and renders blank without it.
- [ ] The dashboard requires basic authentication from a generated credential; the insecure no-auth mode is not
      used.
- [ ] The proxy sits on the edge network only, and that is the sole non-internal network.
- [ ] Request logs reach the log aggregation stack.
