# 10: Edge reverse proxy with a secured dashboard

**What to build:** a reverse proxy that discovers application containers automatically and routes traffic to them,
with a dashboard a developer can log into to see the live routing table.

**Blocked by:**

- [03](03-compose-foundation-networks-volumes.md)

**Governing sources:**

- [Root instructions](../../AGENTS.md)
- [Build prerequisites](../../docs/build/prerequisites.md)
- [Build plan](../../docs/build/plan.md)
- [Platform conventions](../../docs/platform/conventions.md)
- [Service inventory](../../docs/platform/service-inventory.md)
- [Architecture decision index](../../docs/adr/README.md)

**Status:** done

- [x] The proxy runs with the registry name and discovers backends from container labels, so adding a service needs
      no proxy config change and no restart.
- [x] Container discovery is opt-in: services are not exposed unless they ask to be.
- [x] The Docker socket is mounted read-only.
- [x] The dashboard listens on its own entrypoint, separate from the traffic entrypoint.
- [x] The dashboard router matches both the dashboard path and the API path, because the dashboard is a
      single-page app that calls the API and renders blank without it.
- [x] The dashboard requires basic authentication from a generated credential; the insecure no-auth mode is not
      used.
- [x] The proxy sits on the edge network only. That network is already non-internal, which is why the proxy joins
      no access zone; it is not the platform's only non-internal network, because
      [ADR-0021](../../docs/adr/0021-access-zone-for-published-ports.md) added one access zone per environment.
- [x] Request logs reach the log aggregation stack.
