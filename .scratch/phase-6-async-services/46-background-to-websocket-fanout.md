# 46: Background-to-WebSocket event fan-out

**What to build:** a completed background task pushing a live update to the user who triggered it, closing the loop
between the queue and the socket without the client polling.

**Blocked by:** 42, 40.

**Status:** ready-for-agent

- [ ] A worker can publish to a user's notification group without holding a request or an event loop of its own.
- [ ] The publishing helper is safe to call from synchronous task code.
- [ ] A demonstrable end-to-end path exists: a request enqueues work, the worker completes it, and a connected
      client receives a notification.
- [ ] Publishing to a user with no open socket is a no-op, not an error.
- [ ] Failure to publish does not fail the task, and is logged.
- [ ] The event envelope matches the WebSocket message contract, so a client parses one shape everywhere.
- [ ] An integration test runs the full path across processes: enqueue, execute in a real worker, receive on a real
      socket.
- [ ] The test is bounded by a timeout so a missing message fails fast rather than hanging the suite.
