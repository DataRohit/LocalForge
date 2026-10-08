# 90: Authentication workflows

**What to build:** Registration, account activation, resend activation, sign-in, and sign-out screens against the
frozen browser-auth contract.

**Blocked by:**

- [89: App shell, routing, and session](89-app-shell-routing-and-session.md)

**Governing sources:**

- [Frontend standard](../../docs/platform/frontend-standard.md)
- [First-party account endpoints ADR](../../docs/adr/0017-first-party-account-endpoints.md)
- [OpenAPI document](../../docs/api/openapi-v1.yaml)
- [Ticket index](../README.md)

**Status:** pending

- [ ] Each screen handles every documented status code of its endpoint with a visible, accessible outcome.
- [ ] Field errors from the backend attach to their fields; client validation never contradicts the backend.
- [ ] Enumeration-resistant responses are shown without revealing whether an account exists.
- [ ] The `/activate` route reads `account` and `token` from the URL fragment, removes them from the address bar and
      history before anything else renders, and submits them in the activation request body.
- [ ] Routes follow the client route map in the Phase 12 architecture.
- [ ] Sign-out clears session state and the open notification socket.
- [ ] Status recorded as Locally Verified.
