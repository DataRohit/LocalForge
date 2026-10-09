# 82: Cookie-authenticated REST

**What to build:** Let every existing authenticated REST route accept the access cookie through one additional
authentication class, with throttling and status-code documentation unchanged in shape.

**Blocked by:**

- [81: CSRF, cookie attributes, and origin policy](81-csrf-cookie-attributes-and-origin-policy.md)

**Governing sources:**

- [Browser authentication ADR](../../docs/adr/0025-browser-authentication.md)
- [API error contract ADR](../../docs/adr/0018-api-error-contract.md)
- [OpenAPI document](../../docs/api/openapi-v1.yaml)
- [Ticket index](../README.md)

**Status:** done (Locally Verified)

- [x] Every authenticated route accepts a valid access cookie and rejects an expired, malformed, or revoked one
      with its documented status code.
- [x] Inactive and deleted accounts are rejected on the cookie path exactly as on the bearer path.
- [x] A request carrying both an `Authorization` header and the access cookie is rejected with the documented
      bad-request envelope; exactly one credential source is ever accepted.
- [x] Throttling identities treat cookie and bearer requests from one account consistently.
- [x] The regenerated OpenAPI document lists the cookie security scheme and every status code per route.
- [x] Verified in `testing` only; status Locally Verified.

**Verification (2026-10-09, `testing` only):** `backend/tests/integration/accounts/test_browser_cookie_authentication.py`
covers acceptance on the profile routes, expired, malformed, refresh-in-access, and password-revoked cookies, inactive and
deleted accounts compared with the bearer response, a primary outage (503), the dual-source 400 for both bearer schemes,
and shared throttle identity between cookie and bearer requests. The regenerated OpenAPI document lists
`browserCookieAuth` beside `jwtAuth` and `tokenAuth` on every authenticated operation. Findings: none open.
Disposition: complete.
