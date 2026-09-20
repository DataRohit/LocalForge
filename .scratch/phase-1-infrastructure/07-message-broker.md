# 07: Message broker for background work

**What to build:** a running message broker with its own virtual host, a dedicated user, durable storage, and a
management UI a developer can log into to watch queues.

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

- [x] The broker runs with the registry name and a dedicated user, password, and virtual host from the environment.
- [x] The default guest account cannot be used from anywhere but the loopback interface, or is removed.
- [x] Broker state persists to its named volume across a restart.
- [x] The health check is the vendor-documented staged check that confirms the runtime is running and no local
      alarms are raised.
- [x] The health check interval is generous, because each invocation joins and leaves the cluster and is expensive.
- [x] The management UI is reachable from the host and requires the configured credentials.
- [x] The AMQP port is reachable from the host for the host-mode test run.
- [x] The pinned series is confirmed to still be within community support at the time the work is done; if it is
      not, the newer series is pinned and `docs/platform/service-inventory.md` is updated.
