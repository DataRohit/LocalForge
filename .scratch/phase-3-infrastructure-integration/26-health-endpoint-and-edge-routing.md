# 26: Health endpoint and edge routing

**What to build:** the endpoint a load balancer calls to decide whether this instance may serve traffic. It reports
the state of every backing service, and the reverse proxy routes to the application and uses it to take an
unhealthy instance out of rotation.

**Blocked by:**

- [10](../phase-1-infrastructure/10-edge-reverse-proxy.md)
- [19](19-database-and-replica-routing.md)
- [20](20-cache-integration.md)
- [21](21-channel-layer-integration.md)
- [22](22-task-queue-integration.md)
- [23](23-object-storage-integration.md)
- [24](24-email-backend-integration.md)

**Governing sources:**

- [Root instructions](../../AGENTS.md)
- [Build plan](../../docs/build/plan.md)
- [Platform conventions](../../docs/platform/conventions.md)
- [Service inventory](../../docs/platform/service-inventory.md)
- [Architecture decision index](../../docs/adr/README.md)

**Status:** done

- [x] A health route reports overall status with a machine-readable body, and returns a non-success status when any
      required dependency is down, so a load balancer can act on the status code alone.
- [x] The check covers the primary database, the replica, the cache, the channel layer, the broker, object storage,
      and the mail backend, each reported individually.
- [x] A distinction is drawn between liveness — the process is up — and readiness — it can serve traffic. A
      dependency outage makes the instance not ready without killing it.
- [x] The endpoint responds within a bounded time even when a dependency is hanging, because each probe carries its
      own timeout. A load balancer probe must never block on a dead dependency.
- [x] The endpoint is cheap enough to be polled every few seconds and does not run an expensive query per call.
- [x] The response body leaks no credentials, hostnames, or version details to an unauthenticated caller; detail is
      available only to an authorised caller or on an internal-only path.
- [x] The reverse proxy discovers the application by label and routes traffic to it.
- [x] The host name the proxy is exercised with is accepted by the application: the build plan's proxy gate sends
      `Host: localforge.localhost`, which must appear in the allowed hosts and, for browser use, in the trusted
      origins beside the existing `http://localhost:8080`.
- [x] The proxy declares the application as a dependency, so the startup order matches the tier the inventory
      assigns it. [Ticket 10](../phase-1-infrastructure/10-edge-reverse-proxy.md) left `traefik-tk2jp` without one
      because no backend existed to depend on yet.
- [x] The proxy uses the health endpoint for its own backend health checking, and an instance failing it stops
      receiving traffic.
- [x] The container health check uses the same endpoint, so Compose ordering and proxy routing agree.
- [x] Integration tests cover the healthy case and at least one degraded case with a dependency stopped, asserting
      both the status code and the per-dependency detail.
- [x] The endpoint is documented in the schema with every status code it can return.
