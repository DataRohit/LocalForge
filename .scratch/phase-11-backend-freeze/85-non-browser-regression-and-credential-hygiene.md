# 85: Non-browser regression and credential hygiene

**What to build:** Prove the bearer routes, token routes, and subprotocol WebSocket behave exactly as before, and
that no browser credential leaks through any channel.

**Blocked by:**

- [83: WebSocket cookie authentication](83-websocket-cookie-authentication.md)
- [84: Frontend contract gaps](84-frontend-contract-gaps.md)

**Governing sources:**

- [Browser authentication ADR](../../docs/adr/0025-browser-authentication.md)
- [Security audit](../../docs/security/security-audit.md)
- [Ticket index](../README.md)

**Status:** pending

- [ ] The existing bearer, token, and subprotocol test suites pass unchanged, and a schema diff shows no change to
      their documented contracts.
- [ ] Tests prove credentials never appear in a response body, URL, log record, metric label, or error envelope on
      any browser route.
- [ ] The security audit gains checks for cookie attributes, CSRF enforcement, and credential log hygiene.
- [ ] The complete `testing` gate passes in container and host mode with parallel execution.
- [ ] Status recorded as Locally Verified.
