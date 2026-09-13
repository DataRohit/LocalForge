---
status: accepted
date: 2026-09-13
---

# WebSocket authentication over the subprotocol header

WebSocket connections authenticate with the same JWT the REST API issues, carried in the **WebSocket subprotocol
header**, validated by custom ASGI middleware. The query string is not used.

## Why custom middleware at all

Verified 2026-09-13: Channels 4.3.x has **no built-in JWT support**. Its bundled `AuthMiddlewareStack` is session
and cookie based, which does not match an API whose clients hold bearer tokens. Something must be written.

Channels' own documentation shows a `QueryAuthMiddleware` pattern for this — and labels it **"(insecure)"** in the
docs themselves. The reason is that query strings are logged by proxies, retained in browser history, and leak
through `Referer` headers. Our platform ships a reverse proxy that logs requests and a log aggregation stack that
stores them, so a token in a query string would be written to disk by design.

## The shape

Subclass Channels' `AuthMiddleware`. Read the token from `scope["subprotocols"]`. Validate it with SimpleJWT's
`JWTAuthentication().get_validated_token()` and `.get_user()`, wrapped in `database_sync_to_async` so the event
loop is never blocked by the user lookup. Put the resolved user in the connection scope.

The server must echo the accepted subprotocol back on accept, or browsers reject the handshake.

A rejected connection closes with a close code that distinguishes *why* — expired, malformed, absent, inactive
account, unknown user — per the contract in ticket 41. A client must be able to tell "refresh your token and retry"
from "stop retrying".

## Considered options

**Query-string token**, the widely-copied pattern. Rejected on the logging argument above; Channels labels it
insecure in its own docs. If tooling ever forces it, it may return only as a fallback that is disabled by default,
with short-lived access tokens and explicit scrubbing of the token parameter from proxy and ASGI access logs.

**A cookie-based session with `AuthMiddlewareStack`.** Works in a browser and is the path of least resistance, but
it splits the authentication model: REST clients hold tokens, socket clients would need a session. It also drags
CSRF concerns into the socket handshake.

**A ticket endpoint** — POST to get a single-use short-lived ticket, present it on connect. Strictly the most
secure option and the right answer at scale, since the ticket is worthless if logged. Rejected for now as an extra
round trip and an extra endpoint outside the fixed API surface. Recorded as the upgrade path if socket
authentication ever needs hardening beyond this.

**`channels-auth-token-middlewares` 1.3.1**, a third-party package implementing exactly this. Not adopted — the
middleware is roughly forty lines we want to own, test, and keep aligned with our close-code contract, and it adds
a dependency for something that small.

## Consequences

- Access tokens should be short-lived, since a socket credential is presented on every reconnect.
- The middleware must reject an inactive account and a deleted user, not only an invalid signature.
- A test asserts the token never appears in an application log record.
- Error frames on an open socket reuse the envelope from [0018](./0018-api-error-contract.md), so a client parses
  one error shape across HTTP and WebSocket.
