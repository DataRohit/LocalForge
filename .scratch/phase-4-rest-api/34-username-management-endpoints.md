# 34: Username management endpoints

**What to build:** `/users/set_username/`, `/users/reset_username/`, and `/users/reset_username_confirm/` — the
same shape as the password flows, applied to the login identifier.

**Blocked by:**

- [31](31-user-registration-and-profile.md)
- [24](../phase-3-infrastructure-integration/24-email-backend-integration.md)

**Governing sources:**

- [Root instructions](../../AGENTS.md)
- [Build plan](../../docs/build/plan.md)
- [First-party account endpoints ADR](../../docs/adr/0017-first-party-account-endpoints.md)
- [API error contract ADR](../../docs/adr/0018-api-error-contract.md)
- [Platform conventions](../../docs/platform/conventions.md)

**Status:** done

- [x] Setting a username requires authentication and the caller's current password.
- [x] The new username is validated for format and for case-insensitive uniqueness, returning per-field errors in
      the standard envelope.
- [x] Taking a username already in use returns a validation error that does not confirm which account holds it
      beyond the fact that it is unavailable.
- [x] Reset accepts an email address and returns the same accepted response regardless of whether an account
      exists.
- [x] The reset email carries a signed, time-limited, single-use token bound to the account.
- [x] Reset confirm accepts the token and the new username, applying the same validation, and rejects an expired,
      malformed, already-used, or foreign token with a distinct documented code for each.
- [x] Changing a username does not invalidate the account's credentials, and this difference from the password
      flow is stated explicitly in the endpoint documentation so integrators are not surprised.
- [x] Both reset endpoints are rate-limited.
- [x] A successful change notifies the account owner by email.
- [x] Integration tests cover the full loop, the uniqueness conflict, token reuse, expiry under frozen time, and
      that a credential issued before the change still works.

## Decisions

- Authenticated change accepts exactly `current_password` and `new_username`; reset confirmation accepts exactly
  `account`, `token`, and `new_username`. Both successful changes return `204`.
- Username format follows Django's maintained Unicode username validator. Availability uses PostgreSQL `LOWER`
  expressions matching the model constraint, and both pre-check and constraint-race conflicts return only
  `That username is not available.` on `new_username`.
- Reset request returns the same timed `202` response for active, inactive, unknown, and token-store-failure
  outcomes. Its separate configured address and recipient rates are `30/hour` and `3/hour`.
- Username-reset tokens use a dedicated-salt Django token generator plus a random nonce, validate against the
  independent username-reset lifetime rather than Django's password-reset timeout, persist only SHA-256 digests,
  and retain nullable-account immutable-subject tombstones for stable replay classification.
- Successful changes consume every outstanding username-reset record and send a credential-free notification.
  They do not change password hashes or credential rows, so existing DRF tokens, JWT access and refresh tokens, and
  Django sessions remain valid.
