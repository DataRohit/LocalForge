# 38: ASGI protocol routing and consumer base

**What to build:** the application serving HTTP and WebSocket on one ASGI entrypoint, with a base consumer class
every future consumer inherits, so connection lifecycle and error handling are written once.

**Blocked by:**

- [21](../phase-3-infrastructure-integration/21-channel-layer-integration.md)
- [27](../phase-4-rest-api/27-api-foundation-and-error-envelope.md)

**Governing sources:**

- [Root instructions](../../AGENTS.md)
- [Build plan](../../docs/build/plan.md)
- [WebSocket contract](../../docs/api/websocket-v1.md)
- [Channels ADR](../../docs/adr/0003-channels-dedicated-valkey.md)
- [API error contract ADR](../../docs/adr/0018-api-error-contract.md)
- [WebSocket authentication ADR](../../docs/adr/0019-websocket-authentication.md)

**Status:** done

- [x] The ASGI entrypoint routes by protocol type, sending HTTP to the framework's handler and WebSocket to the
      project's router.
- [x] The framework application is initialised before any import that touches models, so the app registry is
      populated when consumers load.
- [x] A base consumer handles connect, disconnect, and receive, validates that incoming frames are JSON objects,
      and rejects anything else with a defined close code rather than raising.
- [x] Messages are exchanged as JSON with a declared envelope: a type field and a payload field.
- [x] An unknown message type is answered with an error frame, not a dropped connection.
- [x] Consumers are asynchronous throughout, and any database access uses the async-safe wrapper so the event loop
      is never blocked.
- [x] The origin of a connection is validated, so a browser page on another origin cannot open a socket.
- [x] The WebSocket route is reachable through the reverse proxy, with the upgrade headers passed through.
- [x] Tests connect, exchange a message, and disconnect cleanly, using the project's async test support.
- [x] The base consumer follows the documentation standard, including its attributes and the close codes it raises.
