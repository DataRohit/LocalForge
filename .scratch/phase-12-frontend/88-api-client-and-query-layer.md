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

**Status:** done (Locally Verified)

- [x] Only the `api/` module calls `fetch` or opens a socket; the architecture test from Ticket 87 enforces it.
- [x] Request and response types derive from the generated schema types, and the client resolves every call through
      the generated `operations.ts` table, so an operation or status missing from the schema fails the type check.
- [x] Unsafe requests send `X-CSRFToken` read from the CSRF cookie after `GET browser/csrf/` seeds it, and every non-2xx
      body parses as the error envelope, including throttling (`Retry-After`), request-size, and CSRF failures; a
      non-envelope body or an undocumented status becomes a distinct unexpected-failure error, never silently ignored.
- [x] A network failure becomes an offline error distinct from any HTTP error.
- [x] A `401` on an authenticated request triggers one single-flight `POST browser/refresh/`, retries the original request
      once, and signs out when the refresh or the retry returns `401`; there is no retry loop.
- [x] Tests mock the backend with handlers typed from the schema and cover every documented status code of every
      operation the client exposes, driven from `operations.ts`.
- [x] Status recorded as Locally Verified.

**Verification (2026-10-10, no runtime change):**

- `make frontend-check` passes: 14 test files, 339 tests, coverage 100% for statements (140), branches (77),
  functions (41), and lines (140); documentation, architecture, bundle, and drift checks clean.
- Single network module: `scripts/check-architecture.test.ts` runs against the real tree and fails if any file
  outside `src/api/` uses `fetch`, a socket, browser storage, or `sendBeacon`.
- Types: `RequestBody` and `SuccessBody` derive from the generated schema, and every call resolves through the
  generated `operations` table; endpoint signatures use them, so a schema change breaks the type check.
- Every documented status of every operation the interface calls (15 operations, 137 operation and status pairs)
  has a tested outcome, driven from the generated table: 2xx resolves, every other status raises `ApiError` with
  the envelope, and `problem.test.ts` maps each documented error status to a kind.
- CSRF: seeding happens once for concurrent requests, a missing cookie after seeding and a seeding failure are
  typed errors, and unsafe requests carry `X-CSRFToken`.
- Session recovery: one single-flight refresh and one retry; a refresh or retry `401` announces session loss once;
  no loop; a login `401` does not refresh; a non-401 refresh failure propagates.
- No runtime, Compose, or service change; `development` was not touched.

**Findings and dispositions:**

- MSW 3 renamed the unhandled-request option to `onUnhandledFrame`; the setup uses it so an unmocked request fails
  the test.
- The exhaustive status test initially exercised the refresh path without a refresh mock and sent a body on GET;
  both were test defects, fixed in the test.
- The documentation checker did not treat constructors and accessors as documentable; added, with a test.
- The CSRF cookie name is the frozen default constant; a deployment that renames the cookie through the backend
  environment would need the constant changed (recorded as a residual risk in the handover).

Disposition: complete.
