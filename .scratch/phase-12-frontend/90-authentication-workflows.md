# 90: Authentication workflows

**What to build:** Registration, account activation, resend activation, sign-in, and sign-out screens against the frozen
browser-auth contract.

**Blocked by:**

- [89: App shell, routing, and session](89-app-shell-routing-and-session.md)

**Governing sources:**

- [Frontend standard](../../docs/platform/frontend-standard.md)
- [First-party account endpoints ADR](../../docs/adr/0017-first-party-account-endpoints.md)
- [OpenAPI document](../../docs/api/openapi-v1.yaml)
- [Phase 12 replan record](../../docs/architecture/phase-12-frontend.md)
- [Ticket index](../README.md)

**Status:** pending

- [ ] Each screen handles every documented status code of its endpoint with a visible, accessible outcome, including
      `429` with its `Retry-After`.
- [ ] Field errors from the backend attach to their fields; client validation never contradicts the backend.
- [ ] Enumeration-resistant responses (registration `201`, resend `202`, sign-in `401`) are shown without revealing
      whether an account exists.
- [ ] The `/activate` route submits the fragment `account` and `token` in the activation request body and distinguishes
      the four documented activation-token failures.
- [ ] Routes follow the client route map in the Phase 12 architecture.
- [ ] Sign-out calls `POST browser/logout/`, clears session state and cached data, and leaves the notification socket
      hook nothing to reconnect to.
- [ ] Status recorded as Locally Verified.
