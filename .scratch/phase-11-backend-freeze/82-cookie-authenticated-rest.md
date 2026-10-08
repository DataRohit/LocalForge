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

**Status:** pending

- [ ] Every authenticated route accepts a valid access cookie and rejects an expired, malformed, or revoked one
      with its documented status code.
- [ ] Inactive and deleted accounts are rejected on the cookie path exactly as on the bearer path.
- [ ] A request carrying both an `Authorization` header and the access cookie is rejected with the documented
      bad-request envelope; exactly one credential source is ever accepted.
- [ ] Throttling identities treat cookie and bearer requests from one account consistently.
- [ ] The regenerated OpenAPI document lists the cookie security scheme and every status code per route.
- [ ] Verified in `testing` only; status Locally Verified.
