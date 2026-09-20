# 41: WebSocket error and close-code contract

**What to build:** a documented, tested contract for every way a socket can fail, so a client can distinguish
"retry later" from "your credential is dead, stop retrying" without guessing.

**Blocked by:**

- [40](40-notification-channel-group-broadcast.md)

**Governing sources:**

- [Root instructions](../../AGENTS.md)
- [Build plan](../../docs/build/plan.md)
- [WebSocket contract](../../docs/api/websocket-v1.md)
- [Channels ADR](../../docs/adr/0003-channels-dedicated-valkey.md)
- [API error contract ADR](../../docs/adr/0018-api-error-contract.md)
- [WebSocket authentication ADR](../../docs/adr/0019-websocket-authentication.md)

**Status:** done

- [x] Every close code the server can emit is enumerated with its meaning and whether a client should retry.
- [x] Authentication failures, authorization failures, malformed frames, unknown message types, oversized frames,
      rate-limited connections, and server errors each map to a distinct, documented code.
- [x] Application-level errors are returned as error frames on an open socket where the connection can continue,
      and reserved for close codes only where it cannot.
- [x] Error frames share one envelope with a stable machine-readable code, matching the REST error contract so a
      client parses one shape.
- [x] A frame size limit is enforced, so an oversized message cannot exhaust memory.
- [x] Connections are rate-limited per user, and the limit is configurable from the environment.
- [x] An unhandled exception in a consumer closes with the server-error code and is logged with the request
      identifier, without leaking a traceback to the client.
- [x] The contract is published in the same documentation as the REST status codes, so a client integrator reads
      one page.
- [x] Tests assert the exact close code for each enumerated failure.
