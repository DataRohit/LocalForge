# 07: Message broker for background work

**What to build:** a running message broker with its own virtual host, a dedicated user, durable storage, and a
management UI a developer can log into to watch queues.

**Blocked by:** 03.

**Status:** ready-for-agent

- [ ] The broker runs with the registry name and a dedicated user, password, and virtual host from the environment.
- [ ] The default guest account cannot be used from anywhere but the loopback interface, or is removed.
- [ ] Broker state persists to its named volume across a restart.
- [ ] The health check is the vendor-documented staged check that confirms the runtime is running and no local
      alarms are raised.
- [ ] The health check interval is generous, because each invocation joins and leaves the cluster and is expensive.
- [ ] The management UI is reachable from the host and requires the configured credentials.
- [ ] The AMQP port is reachable from the host for the host-mode test run.
- [ ] The pinned series is confirmed to still be within community support at the time the work is done; if it is
      not, the newer series is pinned and `docs/platform/service-inventory.md` is updated.
