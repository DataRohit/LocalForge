# 33: Password management endpoints

**What to build:** `/users/set_password/`, `/users/reset_password/`, and `/users/reset_password_confirm/` — an
authenticated user changes their password, and a locked-out user recovers by email.

**Blocked by:** 31, 24.

**Status:** ready-for-agent

- [ ] Setting a password requires the caller to be authenticated and to supply their current password.
- [ ] The new password is validated against the configured validators and must differ from the current one.
- [ ] Changing a password invalidates existing sessions and credentials, so a stolen token stops working. The
      caller's own session is handled deliberately, and the choice is documented.
- [ ] Reset accepts an email address and returns the same accepted response whether or not the account exists, in
      the same time, so accounts cannot be enumerated.
- [ ] The reset email carries a signed, time-limited, single-use token bound to the account and invalidated by a
      password change.
- [ ] Reset confirm accepts the token and a new password, applies the validators, and rejects an expired,
      malformed, already-used, or foreign token with a distinct documented code for each.
- [ ] Both reset endpoints are rate-limited per address and per account.
- [ ] A successful reset notifies the account owner by email that their password changed.
- [ ] No endpoint in this set ever returns a password, a token, or a hash.
- [ ] Integration tests cover the full recovery loop against the mail capture service, credential invalidation
      after change, reuse of a consumed token, expiry under frozen time, and every validation failure.
