# 39: WebSocket authentication

**What to build:** WebSocket connections that carry an authenticated user, established from the same credentials
the REST API issues, so a consumer knows who is connected and an anonymous socket is refused.

**Blocked by:** 38, 30.

**Status:** ready-for-agent

- [ ] Custom middleware resolves the connecting user from the project's token, since the framework's bundled
      middleware is cookie and session based and does not understand the API's credentials.
- [ ] The resolved user is placed in the connection scope and is available to every consumer.
- [ ] An expired, malformed, revoked, or absent credential results in the connection being rejected with a defined
      close code, distinguishable from a server error.
- [ ] An inactive account cannot open a socket even with an otherwise valid credential.
- [ ] The credential is accepted from a mechanism that does not place it in the URL query string, because query
      strings are logged by proxies and stored in browser history; if a query-string fallback is retained for
      tooling, the risk is documented and it is disabled in the production-shaped configuration.
- [ ] Token lookup does not block the event loop.
- [ ] Close codes for each rejection reason are enumerated and documented alongside the REST status codes.
- [ ] Tests cover a valid credential, an expired one, a malformed one, a missing one, an inactive account, and a
      credential belonging to a deleted user.
- [ ] A test asserts that the credential never appears in an application log record.
