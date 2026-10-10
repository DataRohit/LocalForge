# 88: API client and query layer

**What to build:** The single API client, error-envelope parser, CSRF handling, single-flight session refresh, schema-
derived types, and the TanStack Query setup every feature will use.

**Blocked by:**

- [87: Frontend scaffold and toolchain](87-frontend-scaffold-and-toolchain.md)

**Governing sources:**

- [Frontend standard](../../docs/platform/frontend-standard.md) (API access)
- [API error contract ADR](../../docs/adr/0018-api-error-contract.md)
- [OpenAPI document](../../docs/api/openapi-v1.yaml)
- [Browser authentication ADR](../../docs/adr/0025-browser-authentication.md)
- [Phase 12 replan record](../../docs/architecture/phase-12-frontend.md)
- [Ticket index](../README.md)

**Status:** pending

- [ ] Only the `api/` module calls `fetch` or opens a socket; the architecture test from Ticket 87 enforces it.
- [ ] Request and response types derive from the generated schema types, and the client resolves every call through
      the generated `operations.ts` table, so an operation or status missing from the schema fails the type check.
- [ ] Unsafe requests send `X-CSRFToken` read from the CSRF cookie after `GET browser/csrf/` seeds it, and every non-2xx
      body parses as the error envelope, including throttling (`Retry-After`), request-size, and CSRF failures; a
      non-envelope body or an undocumented status becomes a distinct unexpected-failure error, never silently ignored.
- [ ] A network failure becomes an offline error distinct from any HTTP error.
- [ ] A `401` on an authenticated request triggers one single-flight `POST browser/refresh/`, retries the original request
      once, and signs out when the refresh or the retry returns `401`; there is no retry loop.
- [ ] Tests mock the backend with handlers typed from the schema and cover every documented status code of every
      operation the client exposes, driven from `operations.ts`.
- [ ] Status recorded as Locally Verified.
