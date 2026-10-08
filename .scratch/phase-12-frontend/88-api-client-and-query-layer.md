# 88: API client and query layer

**What to build:** The single API client, error-envelope parser, CSRF handling, schema-derived types, and the
TanStack Query setup every feature will use.

**Blocked by:**

- [87: Frontend scaffold and toolchain](87-frontend-scaffold-and-toolchain.md)

**Governing sources:**

- [Frontend standard: API access](../../docs/platform/frontend-standard.md)
- [API error contract ADR](../../docs/adr/0018-api-error-contract.md)
- [OpenAPI document](../../docs/api/openapi-v1.yaml)
- [Ticket index](../README.md)

**Status:** pending

- [ ] Only the API module calls `fetch`; an architecture test enforces it.
- [ ] Request and response types derive from the committed OpenAPI document and fail the type check on drift.
- [ ] Unsafe requests send the CSRF header, and every non-2xx body parses as the error envelope, including
      throttling, request-size, and CSRF failures.
- [ ] A `401` clears session state without a retry loop.
- [ ] Tests mock the backend and cover every documented status code class for the client.
- [ ] Status recorded as Locally Verified.
