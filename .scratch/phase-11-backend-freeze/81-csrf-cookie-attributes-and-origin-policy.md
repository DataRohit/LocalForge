# 81: CSRF, cookie attributes, and origin policy

**What to build:** Enforce CSRF on cookie-authenticated unsafe requests and browser sign-in, and fix the cookie
attributes and trusted-origin policy, including behavior on plain-HTTP local development.

**Blocked by:**

- [80: Browser session endpoints](80-browser-session-endpoints.md)

**Governing sources:**

- [Browser authentication ADR](../../docs/adr/0025-browser-authentication.md)
- [Public edge and Resend ADR](../../docs/adr/0022-public-edge-and-resend.md)
- [Conventions: public deployment profile](../../docs/platform/conventions.md)
- [Ticket index](../README.md)

**Status:** done (Locally Verified)

- [x] An unsafe request authenticated by cookie without a valid CSRF token or trusted origin is rejected with a
      documented, enveloped status code.
- [x] Bearer requests are not subject to CSRF and behave exactly as before.
- [x] Cookie flags, prefixes, `SameSite=Strict`, paths, and lifetimes match the Phase 11 cookie table; the flags
      and paths are fixed in code, and only the three names come from the environment.
- [x] Dated browser evidence records which browsers accept the `Secure` cookies on the plain-HTTP local origin; the
      flags are not relaxed for any browser that rejects them.
- [x] Anti-clickjacking headers (`X-Frame-Options: DENY`, `frame-ancestors 'none'`) are present on API responses.
- [x] Trusted origins and the credentialed CORS stance are documented, and no wildcard is possible.
- [x] Tests cover missing, mismatched, replayed, and cross-origin CSRF cases.
- [x] Verified in `testing` only; status Locally Verified.

**Verification (2026-10-09, `testing` only):** CSRF, origin, cookie-flag, and anti-clickjacking behavior is proven by
`test_login_requires_a_matching_token_and_a_trusted_origin`, `test_an_unsafe_cookie_request_needs_a_valid_token_and_origin`
(missing, mismatched, foreign-browser, absent-origin, cross-origin), `test_bearer_requests_are_exempt_from_csrf`,
`test_api_responses_forbid_framing`, and the settings tests rejecting wildcard trusted origins and unprefixed cookie
names. `security.W016` disappears because the CSRF cookie is now always `Secure`. The dated browser evidence is in
`docs/handover/phase-11.md` Section 3 and is secondary (public bug trackers); it was not reproduced in a browser here
and Phase 12 Playwright must re-verify it. Findings: none open. Disposition: complete.
