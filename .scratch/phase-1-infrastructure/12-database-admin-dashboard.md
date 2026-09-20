# 12: Database administration dashboard

**What to build:** a browser UI where a developer can inspect both database nodes without installing a client, with
both servers already registered on first launch.

**Blocked by:**

- [04](04-postgresql-primary-standby.md)

**Governing sources:**

- [Root instructions](../../AGENTS.md)
- [Build prerequisites](../../docs/build/prerequisites.md)
- [Build plan](../../docs/build/plan.md)
- [Platform conventions](../../docs/platform/conventions.md)
- [Service inventory](../../docs/platform/service-inventory.md)
- [Architecture decision index](../../docs/adr/README.md)

**Status:** done

- [x] The dashboard runs with the registry name, its own named volume, and is reachable from the host.
- [x] It binds an address that works in an IPv4-only setup rather than relying on the default.
- [x] The bundled mail server is disabled, since mail capture is handled elsewhere.
- [x] Both the primary and the standby are pre-registered from a mounted server definition file.
- [x] Server definitions are applied on every launch, so the registration is declarative rather than a one-time
      side effect of first boot.
- [x] Login requires the generated credential.
- [x] The dashboard sits on the data service zone plus the access zone that publishing a host port requires, and
      on no other service zone, and is excluded from the testing environment. The literal wording predates
      [ADR-0021](../../docs/adr/0021-access-zone-for-published-ports.md), which measured that a network marked
      internal silently drops a published port.
