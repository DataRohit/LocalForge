# 93: Notifications client

**What to build:** The authenticated notification socket hook and the notification feed, following the WebSocket
contract and close-code table.

**Blocked by:**

- [89: App shell, routing, and session](89-app-shell-routing-and-session.md)
- [90: Authentication workflows](90-authentication-workflows.md)

**Governing sources:**

- [WebSocket contract](../../docs/api/websocket-v1.md)
- [Frontend standard](../../docs/platform/frontend-standard.md) (API access and states)
- [Full-stack pivot ADR](../../docs/adr/0024-full-stack-pivot.md)
- [Phase 12 replan record](../../docs/architecture/phase-12-frontend.md)
- [Ticket index](../README.md)

**Status:** done (Locally Verified)

- [x] One hook owns the socket, connects only while signed in, and always cleans up on sign-out and unmount.
- [x] Each documented close code maps to the specified behavior: refresh and reconnect (`4403`), bounded backoff
      (`4500`, `1011`, `1012`), or stop.
- [x] Reconnection uses bounded exponential backoff with jitter and never loops on a stop code.
- [x] The feed shows the live list, an explicit empty state, a connection-status notice, and an offline state.
- [x] New notifications announce through an accessible live region.
- [x] Tests drive the hook with a fake socket for every close code and malformed frame.
- [x] Status recorded as Locally Verified.

**Verification (2026-10-11, no runtime change):**

- `make frontend-check` passes: 38 test files, 737 tests, coverage 100% for statements (672), branches (360),
  functions (190), and lines (666); formatting, lint, types, the OpenAPI drift check, documentation, architecture,
  and bundle checks clean. The initial bundle is 136,667 of 200,000 gzip bytes of JavaScript and 3,302 of 25,000 of
  CSS. The whole suite passed three consecutive runs under the parallel runner.
- One owner: `NotificationConnection` (`features/notifications/connection.ts`) owns the socket, its timers, and its
  state, and `NotificationsProvider` owns the controller. `api/socket.ts` is the only code that creates a
  `WebSocket`; it connects to the same-origin `/ws/notifications/` and offers no subprotocol, so the browser
  authenticates with the access cookie. The architecture test enforces the single socket module.
- Connects only while signed in: the provider enables the connection for a signed-in account that holds
  `notifications:receive`, stops it on sign-out, on going offline, and on unmount (each tested), and tags its state
  with the account so a later account never sees the previous one's notifications (tested end to end).
- Close codes, each driven with a fake socket: 4401, 4402, and 4403 renew the session and reconnect; 4408 waits the
  sixty-second admission window; 4400, 4404, 4405, 4406, 4407, 1002, 1007, and 1009 stop with a message and never
  reconnect by themselves; 4500, 1011, 1012, 1006, 1000, 1001, and any unknown code retry with bounded exponential
  backoff (one second doubling to thirty) with jitter between half and the whole delay. A connection that stays open
  ten seconds resets the counters; three consecutive renewals without a healthy connection stop instead of looping.
- Frames: a malformed message of any kind (binary, invalid JSON, wrong envelope, an error frame) is ignored without
  closing the connection or changing the list. Unknown events are shown as plain text.
- Feed: a connection notice for connecting, live, reconnecting, paused, and stopped (with the reason and a retry
  button); an explicit empty state that says earlier notifications are not stored; a newest-first list capped at 50
  with a clear button; and a polite live region that announces each new notification. Notifications are collected
  while the person is on any page, not only this one.
- No runtime, Compose, or service change; `development` was not touched, and no real socket is opened by any test.

**Findings and dispositions:**

- The contract's retry guidance for 4401 is "obtain an access credential". A browser deletes an expired cookie, so a
  handshake after the access token's five minutes carries no cookie and closes 4401, not 4403. 4401 and 4402 are
  therefore treated like 4403: renew once, then reconnect, bounded by the renewal limit.
- The architecture test caught the route table importing the API module; it now takes its socket type from the
  notifications feature.
- The first integration tests matched the header navigation as well as the feed, and the latest notification text
  appears both in the list and in the live region; the assertions are scoped to the feed list.
- `ConnectionNotice` has an idle case no page route can reach; it is covered by a direct component test rather than
  left uncovered.
- The real notification socket through Traefik, and a cookie handshake with real expiry, are exercised in the browser
  suite (Ticket 96); here the socket is a fake driven through every documented code.

Disposition: complete.
