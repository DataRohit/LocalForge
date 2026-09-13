# 06: Cache and channel layer on two isolated Valkey instances

**What to build:** two independently configured Valkey instances — one for caching and Celery results, one for the
WebSocket channel layer — each password-protected, each reachable from the host, and tuned so that cache pressure
can never evict a channel message.

**Blocked by:** 03.

**Status:** ready-for-agent

- [ ] Two separate instances run with the registry names, on separate host ports.
- [ ] The cache instance has a memory ceiling and an eviction policy; the channel instance has neither, so it never
      drops keys under pressure.
- [ ] Each instance requires its own distinct password, supplied from the environment.
- [ ] Health checks authenticate and confirm the server answers, rather than merely opening a socket.
- [ ] Each instance persists to its own named volume.
- [ ] The cache instance reserves separate logical databases for cache entries and Celery results, so clearing the
      cache cannot destroy pending task results.
- [ ] The reserved database indexes are set from the environment and are documented as required to differ.
- [ ] Both instances are reachable from the host at the ports in `docs/platform/service-inventory.md`.
