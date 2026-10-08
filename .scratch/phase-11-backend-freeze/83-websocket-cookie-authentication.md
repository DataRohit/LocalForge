# 83: WebSocket cookie authentication

**What to build:** Let `/ws/notifications/` authenticate a browser handshake from the access cookie, behind the
exact Origin and Host validators, while keeping the subprotocol path for non-browser clients.

**Blocked by:**

- [82: Cookie-authenticated REST](82-cookie-authenticated-rest.md)

**Governing sources:**

- [Browser authentication ADR](../../docs/adr/0025-browser-authentication.md)
- [WebSocket authentication ADR](../../docs/adr/0019-websocket-authentication.md)
- [WebSocket contract](../../docs/api/websocket-v1.md)
- [Ticket index](../README.md)

**Status:** pending

- [ ] A handshake with a valid access cookie and an allowed Origin connects and receives user-targeted events.
- [ ] A handshake with both a cookie and a credential subprotocol closes `4402`, and the contract document says so.
- [ ] A missing, expired, malformed, or revoked cookie closes with the matching documented close code.
- [ ] A cookie handshake from a disallowed or absent Origin is refused before the consumer starts.
- [ ] Revoking the session closes the open socket or the next frame, and the behavior is documented.
- [ ] The WebSocket contract document lists the cookie path and every close code it can produce.
- [ ] A test proves no credential appears in any application or access log record.
- [ ] Verified in `testing` only; status Locally Verified.
