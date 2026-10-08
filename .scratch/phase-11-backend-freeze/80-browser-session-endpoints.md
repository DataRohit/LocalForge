# 80: Browser session endpoints

**What to build:** The `/api/v1/browser/csrf/`, `login/`, `refresh/`, and `logout/` routes that issue, rotate, and
clear the HttpOnly credential cookies for a browser, leaving the bearer routes unchanged.

**Blocked by:**

- [79: Test-isolation hardening](79-test-isolation-hardening.md)

**Governing sources:**

- [Browser authentication ADR](../../docs/adr/0025-browser-authentication.md)
- [Phase 11 architecture: cookies and routes](../../docs/architecture/phase-11-backend-freeze.md)
- [Conventions: browser variables](../../docs/platform/conventions.md)
- [API error contract ADR](../../docs/adr/0018-api-error-contract.md)
- [First-party account endpoints ADR](../../docs/adr/0017-first-party-account-endpoints.md)
- [Ticket index](../README.md)

**Status:** pending

- [ ] The three cookie-name variables are added to `.env.example`, the secret generator, and the `testing` files,
      validated at startup for their required prefixes; `.env.development` changes only as an inert candidate.
- [ ] Login verifies credentials with the existing throttling and enumeration resistance, sets the access and
      refresh cookies exactly as the Phase 11 cookie table specifies, and returns `200` with the `users/me/`
      profile and no token.
- [ ] Refresh returns `204` after rotating the pair from the refresh cookie, and clears all three cookies on any
      failure.
- [ ] Logout returns `204`, blacklists the refresh token, clears all three cookies, and is safe to repeat.
- [ ] The CSRF route returns `204` with `Cache-Control: no-store`, sets only the readable CSRF cookie, and returns
      no body.
- [ ] Every route documents every status code it can return, with a response example, and passes the schema check.
- [ ] Tests prove no token appears in any body, URL, or log record from these routes.
- [ ] Verified in `testing` only; status Locally Verified.
