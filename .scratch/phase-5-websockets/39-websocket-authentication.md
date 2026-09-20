# 39: WebSocket authentication

**What to build:** WebSocket connections that carry an authenticated user, established from the same credentials
the REST API issues, so a consumer knows who is connected and an anonymous socket is refused.

**Blocked by:**

- [38](38-asgi-routing-and-consumer-base.md)
- [30](../phase-4-rest-api/30-json-web-token-endpoints.md)

**Governing sources:**

- [Root instructions](../../AGENTS.md)
- [Build plan](../../docs/build/plan.md)
- [WebSocket contract](../../docs/api/websocket-v1.md)
- [Channels ADR](../../docs/adr/0003-channels-dedicated-valkey.md)
- [API error contract ADR](../../docs/adr/0018-api-error-contract.md)
- [WebSocket authentication ADR](../../docs/adr/0019-websocket-authentication.md)

**Status:** done

- [x] Custom middleware resolves the connecting user from the project's token, since the framework's bundled
      middleware is cookie and session based and does not understand the API's credentials.
- [x] The resolved user is placed in the connection scope and is available to every consumer.
- [x] An expired, malformed, revoked, or absent credential results in the connection being rejected with a defined
      close code, distinguishable from a server error.
- [x] An inactive account cannot open a socket even with an otherwise valid credential.
- [x] The credential is accepted from a mechanism that does not place it in the URL query string, because query
      strings are logged by proxies and stored in browser history; if a query-string fallback is retained for
      tooling, the risk is documented and it is disabled in the production-shaped configuration.
- [x] Token lookup does not block the event loop.
- [x] Close codes for each rejection reason are enumerated and documented alongside the REST status codes.
- [x] Tests cover a valid credential, an expired one, a malformed one, a missing one, an inactive account, and a
      credential belonging to a deleted user.
- [x] A test asserts that the credential never appears in an application log record.
