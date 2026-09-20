# 40: User notification channel with group broadcast

**What to build:** an authenticated client receiving messages addressed to it. On connect the socket joins a group
derived from the authenticated user, and anything published to that group arrives — including from another process.

**Blocked by:**

- [39](39-websocket-authentication.md)

**Governing sources:**

- [Root instructions](../../AGENTS.md)
- [Build plan](../../docs/build/plan.md)
- [WebSocket contract](../../docs/api/websocket-v1.md)
- [Channels ADR](../../docs/adr/0003-channels-dedicated-valkey.md)
- [API error contract ADR](../../docs/adr/0018-api-error-contract.md)
- [WebSocket authentication ADR](../../docs/adr/0019-websocket-authentication.md)

**Status:** done

- [x] On connect, the consumer joins a group derived from the authenticated user's identifier, and leaves it on
      disconnect.
- [x] Group names are derived deterministically and are safe for the channel layer's naming rules.
- [x] A user cannot subscribe to another user's group by sending a crafted message; group membership is decided by
      the server from the authenticated scope, never from client input.
- [x] Publishing to a user's group delivers to every socket that user has open, and to none belonging to anyone
      else.
- [x] A helper exposes publishing to a user from ordinary synchronous application code, so a view or a task can
      notify without knowing about the channel layer.
- [x] Delivery is proven across two processes, not merely two consumers in one.
- [x] Disconnecting removes group membership, and a message published afterwards is not delivered and does not
      error.
- [x] Tests cover multiple concurrent sockets for one user, isolation between two users, and cross-process
      delivery, and they pass under the parallel runner without cross-talk.
