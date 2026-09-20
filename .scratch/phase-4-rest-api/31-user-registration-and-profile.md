# 31: User registration and profile endpoints

**What to build:** `/users/` and `/users/me/` — anyone can register, an authenticated caller can read and update
their own account, and nobody can read or modify anyone else's.

**Blocked by:**

- [29](29-token-authentication-endpoints.md)
- [30](30-json-web-token-endpoints.md)

**Governing sources:**

- [Root instructions](../../AGENTS.md)
- [Build plan](../../docs/build/plan.md)
- [First-party account endpoints ADR](../../docs/adr/0017-first-party-account-endpoints.md)
- [API error contract ADR](../../docs/adr/0018-api-error-contract.md)
- [Platform conventions](../../docs/platform/conventions.md)

**Status:** done

- [x] Registration creates an inactive account and returns the created representation without any sensitive field.
- [x] Registration requires a password confirmation and applies the configured password validators, returning
      per-field errors in the standard envelope.
- [x] Registering with an existing username or email returns the same response and observes the same configured
      minimum public duration as successful registration, so accounts cannot be enumerated; the real outcome is
      communicated only by the email that follows.
- [x] The profile endpoint returns the authenticated caller's own account and never accepts an identifier that
      would let it return someone else's.
- [x] The profile endpoint supports partial update of the mutable fields only; the active flag, permissions flags,
      password, and identifier are not writable through it.
- [x] Deleting the profile requires the current password and is irreversible; the response documents what is
      retained.
- [x] Listing accounts is unavailable to ordinary callers; a non-staff caller does not receive other accounts.
- [x] Unauthenticated access to the profile endpoint returns the unauthorized code, not a redirect to a login page.
- [x] Registration is rate-limited.
- [x] Integration tests cover registration success, every validation failure, the duplicate cases, profile read,
      partial update, forbidden field update, deletion, and unauthenticated access.

## Decisions

- Registration returns `201` with `username` and normalized `email`; the database identifier and account state are
  omitted so PostgreSQL `LOWER` duplicates can return the identical public status and body.
- Every completed registration `201` observes the configured monotonic `0.200`-second minimum from the earliest DRF
  view boundary; validation, throttle, activation-confirmation, and infrastructure-error responses do not.
- `/users/me/` returns `id`, `username`, and `email`; only `email` is mutable because username changes belong to the
  dedicated route in the fixed surface.
- Successful deletion returns `204`. Its OpenAPI response documents retained detached JWT revocation metadata,
  operational logs, and backups under their existing cleanup and retention policies.
