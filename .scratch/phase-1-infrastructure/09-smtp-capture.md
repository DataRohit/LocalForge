# 09: SMTP capture for local mail

**What to build:** a local SMTP sink that accepts every message the application sends and exposes them in a web UI
and a REST API, so mail can be asserted on in tests and read by a human.

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

- [x] The service runs with the registry name, its own named volume, and both the SMTP and web ports reachable from
      the host.
- [x] The health check uses the binary's own readiness subcommand, because the image carries no HTTP client to call
      the readiness endpoint with.
- [x] Sending a message to the SMTP port makes it retrievable through the REST API.
- [x] The REST API can delete all stored messages, giving tests a clean starting point.
- [x] Captured messages persist across a container restart.
- [x] No mail can leave the machine: the service sits on an internal network.
