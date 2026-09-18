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
- Per-field detail for validation failures, keyed by field name, where each value is a non-empty array of
  non-empty messages. Non-validation errors use an empty detail object. The fixed serializers have no nested
  serializer, list, or dictionary fields, so nested validation objects are not part of this contract.
- The request identifier from [../platform/service-inventory.md](../platform/service-inventory.md), so a user-reported
  failure is traceable to a log line.

Implemented as a custom DRF exception handler plus handlers for the responses DRF never sees. The envelope must
survive the paths that bypass DRF entirely — that is where naive implementations leak an HTML page.

## Status codes that must be documented, not just the happy path

Enumerated here because the ones that never appear in view code are exactly the ones that get forgotten. Each row
must be reachable in a test and present in the schema.

| Status | Raised by | Typical cause |
| --- | --- | --- |
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
| 503 | health, readiness, security admission | a required dependency or authoritative throttle store is down |

Ticket 35 adds cache-backed aggregate throttles without changing the table's error contract. A broad
`600/minute` address admission runs at the outer ASGI HTTP boundary before declared or streamed 413 handling,
preflight, content negotiation, parsing, authentication, and permissions; authentication, recovery, and
account-security operations share `30/minute`; authenticated reads use `120/minute`; anonymous API use uses
`60/minute`. Every `/api/v1/` request is charged once, while health, administration, and non-API traffic is
excluded. Every rejection remains the correlated `throttled` envelope with integer `Retry-After` and UUID
`X-Request-ID` response headers, both declared on every OpenAPI 429 response. The exact PostgreSQL admissions owned
by Tickets 29, 31, 32, 33, and 34 remain authoritative and fail closed independently; the general evictable scopes
fail open during cache loss, and their synchronous Valkey decision runs outside the ASGI event loop.

Every Django and early ASGI API response also carries the content-type-options, frame, referrer, and content
security policy headers. Exact-origin credentialed CORS changes which browser may read a response, not its status or
envelope, so it adds no status variant. Any supplied origin adds `Vary: Origin` on ordinary, 413, and 429 responses,
even when denied or duplicated; duplicate or otherwise ambiguous values are never reflected. Allowed preflight is
bodyless `204` middleware behavior only for resolvable versioned API routes, advertises that resolved operation's
methods, and remains absent from per-view OpenAPI methods. The root boundary-response extension binds that status
to an explicit no-content example and the real middleware test instead of inventing a representation forbidden by
HTTP semantics. Unknown, admin, and health OPTIONS requests route normally.

Ticket 32 adds four stable `400` codes beneath the same envelope: `activation_token_expired`,
`activation_token_foreign`, `activation_token_malformed`, and `activation_token_used`. The foreign response also
covers a correctly signed token whose account or digest record is absent, so it never distinguishes a missing
account from a mismatched one. A consumed token retains an immutable subject after account deletion, so replay
continues to return `activation_token_used`; an unissued, mismatched, or unused orphan remains
`activation_token_foreign`.

Ticket 33 adds the parallel password-recovery codes `password_reset_token_expired`,
`password_reset_token_foreign`, `password_reset_token_malformed`, and `password_reset_token_used`. Used
classification survives account deletion through the immutable subject tombstone. A missing record, missing live
account, changed email, mismatched account, or otherwise invalid current account binding is foreign.

Ticket 34 adds `username_reset_token_expired`, `username_reset_token_foreign`,
`username_reset_token_malformed`, and `username_reset_token_used`. Used classification survives account deletion
through the immutable subject tombstone. Missing records or live accounts, changed account-bound state, and
mismatched account identifiers are foreign; PostgreSQL case-insensitive username conflicts remain the ordinary
`validation_error` with a neutral `new_username` detail.

Three that are easy to miss: **406 and 415** come from content negotiation, **413** is rejected before Django
constructs the request or a parser reads the body, and **403 from CSRF** is middleware or session authentication,
not a permission class. The broad source admission precedes all four framework layers, so malformed JSON, 406,
415, invalid authentication, and unauthenticated protected requests cannot bypass aggregate source accounting. A
route documented only with the codes its own code raises is incomplete.

Ticket 36 also records Django's Host validation as a reachable `400` on every fixed HTTP operation. Each operation
carries the exact correlated `bad_request` example and cites its own parameterized public request, rather than a
global evidence claim that cannot prove every method and route. The health boundary uses the same stable JSON
envelope for invalid Host and unexpected `500` responses; administration and unrelated Django routes retain their
framework representations.

The runtime derives `HEAD` from `GET` for `/api/v1/users/me/` and `/health/`. Both safe operations are therefore
published explicitly with stable identifiers, summaries, security, bodyless response semantics, and only the
statuses a real HEAD request can produce. Profile HEAD is charged to authenticated-read admission and is excluded
from the tighter authentication-write scope. Health HEAD remains unthrottled like health GET.

Every request serializer that rejects undeclared fields publishes `additionalProperties: false`, including both
branches of the registration-or-activation `oneOf`. The health contract publishes separate closed `200` ready and
`503` not-ready schemas: success requires all seven fixed checks to be working, while service unavailability
requires at least one check to be unavailable. Optional staff branches retain bounded diagnostics whose status and
generic error agree. Framework failures retain exact `ErrorEnvelope` references. Contract tests validate
representative and negative objects with a JSON Schema 2020-12 validator and reject cross-status bodies.

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

Registration also preserves its normal `201` status and submitted public body when activation-token persistence
alone fails. A new account and any partial token state roll back together, while new, inactive-email, active-email,
and username-only candidates all schedule equivalent dummy work. Authoritative account persistence, lookup, and
registration-admission outages remain `503` because they cannot safely establish the registration outcome.

Every completed registration outcome returning that indistinguishable `201` also observes the environment's
`DJANGO_USER_REGISTRATION_MINIMUM_RESPONSE_DURATION_SECONDS` monotonic response floor. Timing starts at the earliest
DRF view boundary and sleeps only the remaining duration after validation, password hashing, persistence, and real
or dummy activation work. Validation failures, throttles, activation confirmation, and infrastructure `503`
responses do not wait on the floor. The `0.200`-second default was set on 2026-09-17 from a 30-sample host baseline:
the slowest existing accepted-path median was `0.131551` seconds, so the floor retains approximately 52 percent
margin without imposing a multi-second public delay.

This is a deliberate divergence from "return the most semantically precise code", and it is recorded here so a
future reviewer does not "fix" it.

Password-reset request uses the same `0.200`-second monotonic floor and the same approved statistical criterion:
five warmups followed by thirty measured known and unknown requests, with median delta no larger than the greater
of twenty percent or ten milliseconds. Validation, throttling, token confirmation, and infrastructure failures do
not wait on the floor.

Password-reset token-record insertion alone is not an infrastructure failure visible at the public boundary. The
active-account savepoint rolls back any partial record, publishes the same dummy task shape used by inactive and
unknown outcomes, waits on the same floor, and returns the same `202` body. Common account lookup and admission loss
remain `503` because the service cannot establish the shared outcome safely.

Username-reset request uses the same approved `0.200`-second floor and statistical criterion as password reset.
Active, inactive, unknown, and active token-store-failure outcomes return the same `202` body and publish the same
account-and-bearer task shape, while only an active account with an authoritative digest record can receive mail.
Common lookup and admission loss remain correlated `503` responses.

Token login also treats stored password encodings outside the accepted verification profiles as reset-required.
The login request does not verify those encodings: it runs the same fixed current-cost dummy schedule used for an
unknown account, returns the identical generic `401` envelope, and leaves the account and its password unchanged.
This prevents stored higher-cost or mixed parameters from creating attacker-selected work or disclosing account
state through timing.

The accepted profiles are atomic per configured hasher:

| Hasher | Accepted profile | Other recognized profiles |
| --- | --- | --- |
| Argon2 | Exact current type, version, time, memory, parallelism, and output length | Reset required |
| PBKDF2-SHA256 | Current iterations, or a positive lower iteration count | Lower iterations are runtime-hardened to current equivalent cost; higher iterations require reset |
| PBKDF2-SHA1 | Current iterations, or a positive lower iteration count | Lower iterations are runtime-hardened to current equivalent cost; higher iterations require reset |
| Scrypt | Exact current work factor, block size, and parallelism | Reset required |

No historical multi-parameter Argon2 or Scrypt profile is accepted because this project has no evidence that it
previously issued one. Malformed encodings, retired algorithms, and overlong values are reset-required through the
same bounded schedule. Active accounts with accepted current or lower PBKDF2 credentials retain normal login;
successful accepted legacy credentials upgrade to the preferred current hasher.

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
