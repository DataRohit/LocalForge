# 38: ASGI protocol routing and consumer base

**What to build:** the application serving HTTP and WebSocket on one ASGI entrypoint, with a base consumer class
every future consumer inherits, so connection lifecycle and error handling are written once.

**Blocked by:** 21, 27.

**Status:** ready-for-agent

- [ ] The ASGI entrypoint routes by protocol type, sending HTTP to the framework's handler and WebSocket to the
      project's router.
- [ ] The framework application is initialised before any import that touches models, so the app registry is
      populated when consumers load.
- [ ] A base consumer handles connect, disconnect, and receive, validates that incoming frames are JSON objects,
      and rejects anything else with a defined close code rather than raising.
- [ ] Messages are exchanged as JSON with a declared envelope: a type field and a payload field.
- [ ] An unknown message type is answered with an error frame, not a dropped connection.
- [ ] Consumers are asynchronous throughout, and any database access uses the async-safe wrapper so the event loop
      is never blocked.
- [ ] The origin of a connection is validated, so a browser page on another origin cannot open a socket.
- [ ] The WebSocket route is reachable through the reverse proxy, with the upgrade headers passed through.
- [ ] Tests connect, exchange a message, and disconnect cleanly, using the project's async test support.
- [ ] The base consumer follows the documentation standard, including its attributes and the close codes it raises.
