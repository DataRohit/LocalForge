# 34: Username management endpoints

**What to build:** `/users/set_username/`, `/users/reset_username/`, and `/users/reset_username_confirm/` — the
same shape as the password flows, applied to the login identifier.

**Blocked by:** 31, 24.

**Status:** ready-for-agent

- [ ] Setting a username requires authentication and the caller's current password.
- [ ] The new username is validated for format and for case-insensitive uniqueness, returning per-field errors in
      the standard envelope.
- [ ] Taking a username already in use returns a validation error that does not confirm which account holds it
      beyond the fact that it is unavailable.
- [ ] Reset accepts an email address and returns the same accepted response regardless of whether an account
      exists.
- [ ] The reset email carries a signed, time-limited, single-use token bound to the account.
- [ ] Reset confirm accepts the token and the new username, applying the same validation, and rejects an expired,
      malformed, already-used, or foreign token with a distinct documented code for each.
- [ ] Changing a username does not invalidate the account's credentials, and this difference from the password
      flow is stated explicitly in the endpoint documentation so integrators are not surprised.
- [ ] Both reset endpoints are rate-limited.
- [ ] A successful change notifies the account owner by email.
- [ ] Integration tests cover the full loop, the uniqueness conflict, token reuse, expiry under frozen time, and
      that a credential issued before the change still works.
