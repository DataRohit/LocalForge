---
status: accepted
date: 2026-09-13
---

# One API error envelope, and exhaustive status-code documentation

Every non-2xx response from the REST API returns the same JSON shape, and every route documents **every** status
code it can produce — including those raised by middleware and framework layers rather than by view code — each
with at least one response example in the schema.

This is a contract with client integrators, not a documentation nicety. A client that must special-case DRF's
`{"detail": ...}`, its `{"field": ["error"]}`, and an HTML 500 page is a client that will mishandle one of them.

## The envelope

One shape for every error, so a client parses once:

- A stable machine-readable `code` that clients branch on. Never a localised string, never a raw exception name.
- A human-readable `message`.
- Per-field detail for validation failures, keyed by field name.
- The request identifier from [../platform/service-inventory.md](../platform/service-inventory.md), so a user-reported
  failure is traceable to a log line.

Implemented as a custom DRF exception handler plus handlers for the responses DRF never sees. The envelope must
survive the paths that bypass DRF entirely — that is where naive implementations leak an HTML page.

## Status codes that must be documented, not just the happy path

Enumerated here because the ones that never appear in view code are exactly the ones that get forgotten. Each row
must be reachable in a test and present in the schema.

| Status | Raised by | Typical cause |
|---|---|---|
| 400 | DRF serializer validation | malformed body, failed field validation |
| 401 | authentication classes | missing, malformed, or expired credential |
| 403 | permission classes, CSRF middleware | authenticated but not permitted; CSRF failure on a session-authenticated write |
| 404 | routing, object lookup | unknown route, or an object the caller may not see |
| 405 | the router | method not allowed on an existing route |
| 406 | content negotiation | unacceptable `Accept` header |
| 413 | ASGI request boundary | actual received body bytes exceed the environment-configured API ceiling |
| 415 | content negotiation | unsupported `Content-Type` on a write |
| 429 | throttling | rate limit exceeded, with `Retry-After` |
| 500 | unhandled exception | must return the envelope, never a traceback or an HTML page |
| 503 | health and readiness | a required dependency is down |

Three that are easy to miss: **406 and 415** come from content negotiation, **413** is rejected before Django
constructs the request or a parser reads the body, and **403 from CSRF** is middleware or session authentication,
not a permission class. A route documented only with the codes its own code raises is incomplete.

The request-body ceiling is `DJANGO_API_REQUEST_BODY_MAX_BYTES`. The ASGI boundary rejects an oversized valid
`Content-Length` before receiving body data, then counts every actual `http.request` body chunk so omitted,
malformed, or understated declarations cannot bypass the ceiling. Crossing the limit presents a disconnect to
Django so its request spool closes, then returns `request_too_large` with transport-level request correlation.
Accepted chunks retain normal receive semantics, and non-API routes retain Django's existing behavior. The same
value bounds Django's in-memory request spool.

## Enumeration resistance beats precise status codes

Where the two conflict, resistance wins. Registration, password reset, username reset, and login must not reveal
whether an account exists — not through the status code, not through the body, and not through response timing. So
password reset returns the same accepted response for a known and an unknown address, and the schema says so
explicitly rather than leaving a client to infer it from a 404 that never comes.

This is a deliberate divergence from "return the most semantically precise code", and it is recorded here so a
future reviewer does not "fix" it.

## How it is documented

drf-spectacular's `@extend_schema` with `OpenApiResponse` and `OpenApiExample`, using `status_codes` on each example
to bind it to the response it illustrates. Verified working on drf-spectacular 0.30.0 on 2026-09-13.

The schema is the source of truth: the test suite asserts observed responses against the documented ones, so a
route that grows an undocumented status code fails the build rather than silently drifting.

## Considered options

**DRF's default error shapes.** Free, and already inconsistent between `{"detail": ...}` and field-keyed
dictionaries — which is the problem being solved.

**RFC 9457 problem details.** A real standard with `type`, `title`, `status`, `detail`, `instance`, and genuinely
the right answer for a public API. Rejected for now because it adds a media-type negotiation concern and the
per-field validation shape still needs an extension, so it would not remove the custom handler. Recorded as the
migration target if this API is ever published externally.

**Document only the status codes each view raises.** Rejected: it omits every middleware and negotiation code,
which is precisely the set that surprises integrators.

## Consequences

- One exception handler and a small set of framework-level handlers become load-bearing, and need their own tests
  including a deliberately unhandled exception.
- Every route carries schema annotations. That cost was going to be paid regardless — see
  [0017](./0017-first-party-account-endpoints.md).
- The WebSocket error contract in [0019](./0019-websocket-authentication.md) reuses this envelope, so a client
  parses one error shape across both protocols.
