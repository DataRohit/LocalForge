# 48: Integration test suite

**What to build:** proof that the pieces work together against the real services — real database, real cache, real
broker, real channel layer, real object storage — covering every documented route and every documented status code.

**Blocked by:**

- [47](47-unit-test-suite.md)

**Governing sources:**

- [Root instructions](../../AGENTS.md)
- [Ticket index and phase gates](../README.md)
- [Build plan](../../docs/build/plan.md)
- [Platform conventions](../../docs/platform/conventions.md)
- [Service inventory](../../docs/platform/service-inventory.md)
- [Documentation standard](../../docs/platform/documentation-standard.md)

**Status:** done

- [x] Every route in the API surface has an integration test for each status code documented in the schema,
      including those produced by middleware and framework layers rather than by view code.
- [x] Authentication flows are covered end to end for both credential types: obtain, use, refresh where
      applicable, verify where applicable, and invalidate.
- [x] Account lifecycle is covered end to end: register, receive the activation email, activate, log in, change
      password, reset password, change username, reset username.
- [x] Read-replica routing, cache behaviour, task execution, object upload and retrieval, and mail capture are each
      asserted against the real service.
- [x] Object storage is asserted live, which the phase-1 compose tests cannot reach because they parse the manifest
      where no Docker client exists: the S3 API, master UI, and file browser each answer on their documented host
      ports; an upload read back is byte-identical; and an unauthenticated request to the bucket is refused.
- [x] Mail capture is asserted live, in both run modes: a message sent to the SMTP port is retrievable through the
      REST API, deleting all messages empties the store, and captured mail survives a container recreate.
- [x] WebSocket tests cover connection, authentication success and every failure, group delivery, and cross-process
      delivery.
- [x] The health endpoint is tested healthy and degraded, with a dependency genuinely stopped.
- [x] Each test isolates its own data and cleans up, so the suite passes under the parallel runner with no ordering
      dependence. Running the suite twice in a row passes both times.
- [x] Every test that touches a service has a timeout, so an unreachable dependency fails fast instead of hanging.
- [x] Shared state in external services is namespaced per worker, so parallel workers cannot collide on cache keys,
      queues, buckets, or channel groups.
- [x] Branch coverage across the project meets the enforced threshold with the unit layer.
- [x] The deployed development and testing paths are exercised after the suite, both environments remain healthy,
      and every affected container log is free of unexplained warning-or-higher records.
