# 92: Profile management

**What to build:** View, edit, and delete the signed-in account's profile.

**Blocked by:**

- [90: Authentication workflows](90-authentication-workflows.md)

**Governing sources:**

- [Frontend standard](../../docs/platform/frontend-standard.md)
- [OpenAPI document](../../docs/api/openapi-v1.yaml)
- [Phase 12 replan record](../../docs/architecture/phase-12-frontend.md)
- [Ticket index](../README.md)

**Status:** done (Locally Verified)

- [x] View and edit use the profile endpoint and show backend validation errors on their fields.
- [x] Delete requires the current password and an explicit, accessible confirmation, signs the user out, and clears all
      cached data.
- [x] Optimistic updates are not used for destructive or credential-bearing changes.
- [x] Loading, empty, error, and offline states are implemented and tested.
- [x] Status recorded as Locally Verified.

**Verification (2026-10-11, no runtime change):**

- `make frontend-check` passes: 32 test files, 629 tests, coverage 100% for statements (499), branches (283),
  functions (148), and lines (496); formatting, lint, types, the OpenAPI drift check, documentation, architecture,
  and bundle checks clean. The initial bundle is 134,202 of 200,000 gzip bytes of JavaScript and 3,192 of 25,000 of
  CSS. The whole suite passed three consecutive runs under the parallel runner.
- View: the account page shows the username and email from the session profile. Editing and deleting appear only
  when the backend granted `account:update` and `account:delete`; an account with neither sees an explanation.
- Edit: `PATCH users/me/` carries only the email. The profile in the session changes after the backend answers, never
  before, so there is no optimistic update; backend validation is attached to the field, and every documented status
  (`400` to `503`) has a visible alert, with `401` ending in the sign-in page.
- Delete: a native modal dialog asks for the current password and states that the action cannot be undone. Nothing is
  sent until the person confirms; cancelling or pressing Escape closes it and returns focus to the button that opened
  it. Success signs out, clears every cached query, and the sign-in page says the account was deleted. A wrong
  password stays on its field with the dialog and session intact, and every documented status of the delete
  operation has a tested outcome.
- States: loading (the session splash), error (the session error with a retry that recovers), offline (the shell
  notice, and a failed save or delete keeps what was typed), and empty (no capabilities, or only one of the two).
- No runtime, Compose, or service change; `development` was not touched.

**Findings and dispositions:**

- jsdom implements the `open` attribute but not `showModal` or `close`; a small shim reproduces the open state, the
  close event, and focus moving to the first control. Focus trapping and the backdrop are properties of the native
  element and are covered by the real-browser accessibility and end-to-end tickets (94 and 96).
- The first draft of the null-opener test mocked the active element after the dialog had already captured the
  opener, so it did not reach the branch; it now mocks before opening and uses plain events.
- A new "account deleted" notice joined the sign-in notices from Ticket 91.
- A Dialog renders its content only while open, so a reopened confirmation starts empty and no password lingers.

Disposition: complete.
