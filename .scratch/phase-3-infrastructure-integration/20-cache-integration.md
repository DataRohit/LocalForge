# 20: Cache integration

**What to build:** Django caching into the cache Valkey instance, with sessions stored there too, and a
demonstrated separation between cache keys and task-result keys.

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

- [x] The cache backend is the one shipped with the framework, pointed at the cache instance with credentials and
      the reserved logical database index from the environment.
- [x] The client library the backend imports is installed explicitly, since the framework hard-codes that import
      and a protocol-compatible fork cannot substitute for it.
- [x] A key prefix and a default timeout are configured so cache entries are attributable and expire.
- [x] Sessions use the cache backend.
- [x] Task results use a different logical database from the cache, and an integration test proves that clearing
      the cache leaves task-result keys intact — the framework's clear operation flushes an entire logical
      database.
- [x] Cache failures degrade gracefully: an unavailable cache does not return a server error for a page that only
      reads through the cache.
- [x] Integration tests round-trip a value, confirm expiry, and confirm the prefix is applied.
