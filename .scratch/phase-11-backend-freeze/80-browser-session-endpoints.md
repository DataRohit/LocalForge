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

**Status:** done (Locally Verified)

- [x] The three cookie-name variables are added to `.env.example`, the secret generator, and the `testing` files,
      validated at startup for their required prefixes; `.env.development` changes only as an inert candidate.
- [x] Login verifies credentials with the existing throttling and enumeration resistance, sets the access and
      refresh cookies exactly as the Phase 11 cookie table specifies, and returns `200` with the `users/me/`
      profile and no token.
- [x] Refresh returns `204` after rotating the pair from the refresh cookie, and clears all three cookies on any
      failure.
- [x] Logout returns `204`, blacklists the refresh token, clears all three cookies, and is safe to repeat.
- [x] The CSRF route returns `204` with `Cache-Control: no-store`, sets only the readable CSRF cookie, and returns
      no body.
- [x] Every route documents every status code it can return, with a response example, and passes the schema check.
- [x] Tests prove no token appears in any body, URL, or log record from these routes.
- [x] Verified in `testing` only; status Locally Verified.

**Verification (2026-10-09, `testing` only):** `make check` host gate (2,297 tests, 100.00% coverage) and container
mode through `make testing-test-container`. Route behavior is proven by
`backend/tests/integration/accounts/test_browser_sessions.py` (attributes, rotation, replay, logout, secrecy, and the
observed-versus-documented status comparison for csrf, csrf-head, login, refresh, logout). Findings: the first logout
implementation locked the outstanding-token row across a nullable join and returned 503; fixed with
`select_for_update(of=("self",))`. A form post without a CSRF header would have been parsed by DRF parsers and answered
415; the check now reads the underlying Django request and answers 403
(`test_a_forged_form_post_is_forbidden_not_unsupported`). Disposition: remediated.
