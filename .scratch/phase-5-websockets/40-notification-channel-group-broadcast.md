# 40: User notification channel with group broadcast

**What to build:** an authenticated client receiving messages addressed to it. On connect the socket joins a group
derived from the authenticated user, and anything published to that group arrives — including from another process.

**Blocked by:** 39.

**Status:** ready-for-agent

- [ ] On connect, the consumer joins a group derived from the authenticated user's identifier, and leaves it on
      disconnect.
- [ ] Group names are derived deterministically and are safe for the channel layer's naming rules.
- [ ] A user cannot subscribe to another user's group by sending a crafted message; group membership is decided by
      the server from the authenticated scope, never from client input.
- [ ] Publishing to a user's group delivers to every socket that user has open, and to none belonging to anyone
      else.
- [ ] A helper exposes publishing to a user from ordinary synchronous application code, so a view or a task can
      notify without knowing about the channel layer.
- [ ] Delivery is proven across two processes, not merely two consumers in one.
- [ ] Disconnecting removes group membership, and a message published afterwards is not delivered and does not
      error.
- [ ] Tests cover multiple concurrent sockets for one user, isolation between two users, and cross-process
      delivery, and they pass under the parallel runner without cross-talk.
