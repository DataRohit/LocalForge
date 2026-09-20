# 21: Channel layer integration

**What to build:** the Channels layer wired to its dedicated Valkey instance, so a message published by one
application process is received by a consumer in another. This is the property that later makes horizontal scaling
work, so it is proven now, across two processes.

**Blocked by:**

- [06](../phase-1-infrastructure/06-valkey-cache-and-channel-layer.md)
- [17](../phase-2-project-foundation/17-application-image-and-entrypoint.md)

**Governing sources:**

- [Root instructions](../../AGENTS.md)
- [Build plan](../../docs/build/plan.md)
- [Platform conventions](../../docs/platform/conventions.md)
- [Service inventory](../../docs/platform/service-inventory.md)
- [Architecture decision index](../../docs/adr/README.md)

**Status:** done

- [x] The channel layer backend is the publish/subscribe variant, pointed at the dedicated channel instance with
      credentials from the environment.
- [x] The layer is configured with a host list rather than a single host string, so moving to a clustered instance
      later is a configuration change.
- [x] A group prefix is configured so channel keys are attributable.
- [x] An integration test sends to a group from one connection and receives it on another.
- [x] A test proves cross-process delivery, not merely cross-consumer delivery within one process.
- [x] The testing environment uses the same backend as development, so the layer is genuinely exercised rather
      than replaced by an in-memory stand-in.
- [x] Async tests use the project's async test support and run under the parallel runner without interfering.
- [x] If the layer package fails on this Python version, the escape recorded in
      `docs/adr/0016-accept-release-lag.md` is applied and the resolution written back into that record.
