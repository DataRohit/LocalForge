# 32: Account activation and resend

**What to build:** `/users/resend_activation/` and the activation confirmation it drives — a new account is
unusable until the owner proves they control the email address.

**Blocked by:** 31, 24.

**Status:** ready-for-agent

- [ ] Registration triggers an activation email containing a signed, time-limited, single-use token bound to the
      account.
- [ ] Confirming activation with a valid token activates the account, and the token cannot be reused afterwards.
- [ ] An expired, malformed, already-used, or foreign token is rejected with a distinct documented error code for
      each case, without revealing whether the referenced account exists.
- [ ] Resend accepts an email address and returns the same accepted response whether or not an account exists,
      whether or not it is already active, so the endpoint cannot be used to probe for accounts.
- [ ] Resend is rate-limited per address and per account, so it cannot be used to flood someone's inbox.
- [ ] An already-active account receiving a resend request is not sent a new activation link.
- [ ] Activation links are built from the environment's site host, so they are correct per environment.
- [ ] The email renders both a plain-text and an HTML part.
- [ ] The email is sent by a background task, after the database transaction commits, so a rolled-back registration
      never sends one.
- [ ] Integration tests cover the full loop against the mail capture service, plus every rejection case using
      frozen time for expiry.
