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

**Status:** pending

- [ ] An unsafe request authenticated by cookie without a valid CSRF token or trusted origin is rejected with a
      documented, enveloped status code.
- [ ] Bearer requests are not subject to CSRF and behave exactly as before.
- [ ] Cookie flags, prefixes, `SameSite=Strict`, paths, and lifetimes match the Phase 11 cookie table; the flags
      and paths are fixed in code, and only the three names come from the environment.
- [ ] Dated browser evidence records which browsers accept the `Secure` cookies on the plain-HTTP local origin; the
      flags are not relaxed for any browser that rejects them.
- [ ] Anti-clickjacking headers (`X-Frame-Options: DENY`, `frame-ancestors 'none'`) are present on API responses.
- [ ] Trusted origins and the credentialed CORS stance are documented, and no wildcard is possible.
- [ ] Tests cover missing, mismatched, replayed, and cross-origin CSRF cases.
- [ ] Verified in `testing` only; status Locally Verified.
