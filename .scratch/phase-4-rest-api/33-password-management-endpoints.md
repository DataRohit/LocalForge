# 33: Password management endpoints

**What to build:** `/users/set_password/`, `/users/reset_password/`, and `/users/reset_password_confirm/` — an
authenticated user changes their password, and a locked-out user recovers by email.

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

- [x] Setting a password requires the caller to be authenticated and to supply their current password.
- [x] The new password is validated against the configured validators and must differ from the current one.
- [x] Changing a password invalidates existing sessions and credentials, so a stolen token stops working. The
      caller's own session is handled deliberately, and the choice is documented.
- [x] Reset accepts an email address and returns the same accepted response whether or not the account exists, in
      the same time, so accounts cannot be enumerated.
- [x] The reset email carries a signed, time-limited, single-use token bound to the account and invalidated by a
      password change.
- [x] Reset confirm accepts the token and a new password, applies the validators, and rejects an expired,
      malformed, already-used, or foreign token with a distinct documented code for each.
- [x] Both reset endpoints are rate-limited per address and per account.
- [x] A successful reset notifies the account owner by email that their password changed.
- [x] No endpoint in this set ever returns a password, a token, or a hash.
- [x] Integration tests cover the full recovery loop against the mail capture service, credential invalidation
      after change, reuse of a consumed token, expiry under frozen time, and every validation failure.

## Decisions

- Password change accepts exactly `current_password`, `new_password`, and `new_password_confirm`; reset confirmation
  accepts exactly `account`, `token`, `new_password`, and `new_password_confirm`.
- Every successful password replacement invalidates the caller too. DRF tokens are deleted, outstanding refresh
  tokens are blacklisted, JWT password claims cease matching, and Django session authentication hashes cease
  matching. Clients authenticate again after the `204`.
- Reset request returns `202` with one body for active, inactive, and unknown addresses. Every accepted response
  observes a `0.200`-second monotonic floor; the approved five-warmup, thirty-sample test permits the greater of
  twenty percent or ten milliseconds median delta. Reset-token insert failure is also an accepted dummy-publication
  outcome: any partial active-account record rolls back, while common lookup or admission loss remains `503`.
- Reset bearers combine Django's password-reset token generator with a random issuance nonce. Only SHA-256 digests,
  immutable subjects, use state, and durable delivery state are stored.
- Reset request and confirmation use separate primary-backed `30/hour` address dimensions and `3/hour` recipient or
  account dimensions. Exact invalid bodies record no admission; PostgreSQL loss fails closed.
- Delivery claims one SMTP attempt durably, expires with the reset token, and revalidates locked account and token
  state. SMTP failure recovers through another reset request.
- Used records remain classification tombstones after account deletion.
  [Ticket 43](../phase-6-async-services/43-periodic-task-scheduler.md) owns bounded cleanup after the configured
  maximum age.
- Reset confirmation runs exact-shape and account-independent password validation before admission. It authenticates
  and locks the bearer and account before running the complete validator set against the real locked account, so
  malformed, foreign, expired, and used classification cannot depend on account attributes. Account-independent
  invalid bodies consume no quota; post-admission account-sensitive validation does.
- DRF token login and JWT create complete their bounded enumeration-resistant password schedules without an account
  row lock, then carry an immutable account ID and exact password-security snapshot into issuance. The issuance
  transaction locks the account, rejects changed or inactive state generically, and only then persists credentials.
  Password replacement retains account-first locking, so either issuance commits first and is revoked or replacement
  commits first and stale issuance is rejected. Concurrent existing and unknown wrong-password batches use the same
  approved timing bound without per-account hash serialization. JWT rotation likewise locks the account before its
  outstanding token, preserving atomic blacklist and replacement persistence without an opposing lock order.
- Confirmation rechecks expiry after account and token locks. Used state has precedence, then locked expiry, then
  account binding, so a bearer aging out during lock wait returns `password_reset_token_expired`.
