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

**Status:** pending

- [ ] One hook owns the socket, connects only while signed in, and always cleans up on sign-out and unmount.
- [ ] Each documented close code maps to the specified behavior: refresh and reconnect (`4403`), bounded backoff
      (`4500`, `1011`, `1012`), or stop.
- [ ] Reconnection uses bounded exponential backoff with jitter and never loops on a stop code.
- [ ] The feed shows the live list, an explicit empty state, a connection-status notice, and an offline state.
- [ ] New notifications announce through an accessible live region.
- [ ] Tests drive the hook with a fake socket for every close code and malformed frame.
- [ ] Status recorded as Locally Verified.
