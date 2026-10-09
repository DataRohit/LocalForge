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

**Status:** done (Locally Verified)

- [x] A handshake with a valid access cookie and an allowed Origin connects and receives user-targeted events.
- [x] A handshake with both a cookie and a credential subprotocol closes `4402`, and the contract document says so.
- [x] A missing, expired, malformed, or revoked cookie closes with the matching documented close code.
- [x] A cookie handshake from a disallowed or absent Origin is refused before the consumer starts.
- [x] Revoking the session closes the open socket or the next frame, and the behavior is documented.
- [x] The WebSocket contract document lists the cookie path and every close code it can produce.
- [x] A test proves no credential appears in any application or access log record.
- [x] Verified in `testing` only; status Locally Verified.

**Verification (2026-10-09, `testing` only):** `backend/tests/integration/notifications/test_cookie_authentication.py`
(14 cases) covers admission and delivery, the dual-source 4402, every rejection classification, Origin refusal before the
consumer, revocation closing cookie sockets only, per-frame revalidation, expiry closing, a healthy session, and log
secrecy. `docs/api/websocket-v1.md` documents the cookie path and every close code. Findings: importing the
authentication module from the consumer broke the Uvicorn live seam (AppRegistryNotReady); fixed by injecting the
revalidation callable through the scope. Disposition: remediated.
