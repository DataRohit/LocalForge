# 91: Account recovery and credential changes

**What to build:** Password reset request and confirm, username reset request and confirm, and signed-in password and
username change screens.

**Blocked by:**

- [90: Authentication workflows](90-authentication-workflows.md)

**Governing sources:**

- [Frontend standard](../../docs/platform/frontend-standard.md)
- [First-party account endpoints ADR](../../docs/adr/0017-first-party-account-endpoints.md)
- [OpenAPI document](../../docs/api/openapi-v1.yaml)
- [Phase 12 replan record](../../docs/architecture/phase-12-frontend.md)
- [Ticket index](../README.md)

**Status:** done (Locally Verified)

- [x] All six endpoints are reachable from the UI and every documented status code has a handled outcome.
- [x] `/reset-password` and `/reset-username` read `account` and `token` only through `src/session/fragment.ts`, and send
      them only in the request body.
- [x] A successful credential change reflects the new session state without a stale screen.
- [x] Tokens never appear in logs, error reports, or telemetry.
- [x] Status recorded as Locally Verified.

**Verification (2026-10-11, no runtime change):**

- `make frontend-check` passes: 30 test files, 588 tests, coverage 100% for statements (459), branches (261),
  functions (137), and lines (456); formatting, lint, types, the OpenAPI drift check, documentation, architecture,
  and bundle checks clean. The initial bundle is 133,757 of 200,000 gzip bytes of JavaScript and 3,118 of 25,000 of
  CSS. The whole suite passed three consecutive runs under the parallel runner.
- All six endpoints are reachable: forgot-password and forgot-username request a link, reset-password and
  reset-username confirm it, and the signed-in password and username pages sit behind the `credentials:change`
  capability guard and the shell navigation. Every documented status of every one of the six operations is driven
  against the mock backend and has a visible alert with the right role and an enabled submit button afterwards.
- Link credentials: the reset pages read the account and token only from the in-memory fragment credentials, send
  them only in the request body (a test asserts the token is absent from the request URL), send nothing on load, and
  forget them when the token cannot be reused. The four documented token codes of each family give their own message
  and offer a new link, and the backend's wording is not shown for them. A field validation failure keeps the form and
  the credentials, so the person can correct and resubmit.
- Enumeration resistance: the request pages show the backend's acceptance statement verbatim for any address.
- Credential change: a password change signs the person out of the local session with a notice, because the backend
  revokes every session; a username change refetches the profile, so the new name shows on the page and in the
  account at once, and the password field is cleared.
- Tokens are never logged: the pages do not log, and the shared test setup fails any test that writes to the console.
- No runtime, Compose, or service change; `development` was not touched.

**Findings and dispositions:**

- A sign-in notice was added so the guard's redirect after a password change does not swallow the explanation. The
  first version announced "Your session ended" to anonymous visitors, because the client signals session loss on
  the initial profile `401`; a sign-in page test caught it. The notice is now set only when a session existed, with a
  test for the visitor who never had one.
- A registration test queried the "Sign in" link in the whole page while the header briefly held a second one; it is
  scoped to the main region.
- Testing Library's default one-second wait was too tight for a 30-file parallel run; the shared setup now allows
  five seconds, inside the ten-second per-test timeout.
- The reset pages are a container plus a form so the form's action never has to handle absent credentials.
- Both forgot pages share one email screen, and both reset pages share one test table, so the two flows cannot drift.

Disposition: complete.
