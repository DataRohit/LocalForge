# 90: Authentication workflows

**What to build:** Registration, account activation, resend activation, sign-in, and sign-out screens against the frozen
browser-auth contract.

**Blocked by:**

- [89: App shell, routing, and session](89-app-shell-routing-and-session.md)

**Governing sources:**

- [Frontend standard](../../docs/platform/frontend-standard.md)
- [First-party account endpoints ADR](../../docs/adr/0017-first-party-account-endpoints.md)
- [OpenAPI document](../../docs/api/openapi-v1.yaml)
- [Phase 12 replan record](../../docs/architecture/phase-12-frontend.md)
- [Ticket index](../README.md)

**Status:** done (Locally Verified)

- [x] Each screen handles every documented status code of its endpoint with a visible, accessible outcome, including
      `429` with its `Retry-After`.
- [x] Field errors from the backend attach to their fields; client validation never contradicts the backend.
- [x] Enumeration-resistant responses (registration `201`, resend `202`, sign-in `401`) are shown without revealing
      whether an account exists.
- [x] The `/activate` route submits the fragment `account` and `token` in the activation request body and distinguishes
      the four documented activation-token failures.
- [x] Routes follow the client route map in the Phase 12 architecture.
- [x] Sign-out calls `POST browser/logout/`, clears session state and cached data, and leaves the notification socket
      hook nothing to reconnect to.
- [x] Status recorded as Locally Verified.

**Verification (2026-10-11, no runtime change):**

- `make frontend-check` passes: 25 test files, 482 tests, coverage 100% for statements (364), branches (219),
  functions (112), and lines (361); formatting, lint, types, the OpenAPI drift check, documentation, architecture,
  and bundle checks clean. The initial bundle is 132,229 of 200,000 gzip bytes of JavaScript and 3,118 of 25,000 of
  CSS. `make frontend-audit` passes.
- Screens: sign-in, registration, activation, and resend-activation at the routes in the Phase 12 map, with sign-out
  in the shell. Each of the four forms and the activation page is driven against the mock backend for every
  documented status of its operation (`400` to `503`), asserting a visible alert with the right role and an enabled
  submit button afterwards; throttling shows the `Retry-After` wait and being offline keeps what was typed.
- Field errors: backend field names attach to their inputs through `aria-describedby`, the first is focused, and
  messages for names the form lacks appear in the alert. Client validation only checks for empty fields and matching
  passwords, so it cannot contradict the backend.
- Enumeration resistance: registration shows one neutral confirmation ("If the details you entered can be used...")
  and never says an account was created; resend shows the backend's state-independent statement verbatim; a failed
  sign-in shows the backend's single generic message. Tests assert none of them contain wording that distinguishes an
  existing account.
- Activation: the page asks for a click and a test proves no request is sent before it. The account and token come
  from the in-memory fragment credentials, go in the request body only, and are forgotten when the token cannot be
  reused. The four documented token failures each have their own message and offer a resend link; a transient failure
  keeps the credentials so the person can try again.
- Sign-out: `POST browser/logout/`, then the session is dropped and every cached query is cleared; a failed sign-out
  stays signed in and says so (Ticket 89 tests, updated for the real sign-in page). The notification socket arrives
  in Ticket 93 and connects only while signed in, so a signed-out session leaves it nothing to reconnect to.
- No runtime, Compose, or service change; `development` was not touched.

**Findings and dispositions:**

- A Ticket 89 test expected a "Page not found" screen at `/sign-in` because the route did not exist yet; it now
  expects the real sign-in page.
- Two field-error assertions awaited the input and then read its description at once, which raced the server error
  and failed once in a full parallel run; all three now wait on the assertion itself, and the suite passed three
  repeated runs.
- `activate` and `register` now fail loudly as unexpected when the backend answers with the wrong body shape instead
  of passing it on, each covered by a test.
- A null-credentials guard inside the activation submit was unreachable; it was removed instead of tested.
- The initial JavaScript grew from 118 to 132 kB with React Hook Form and Zod in use; 68 kB of budget remains for
  Tickets 91 to 93, and route-level splitting is the planned response if it is needed.

Disposition: complete.
