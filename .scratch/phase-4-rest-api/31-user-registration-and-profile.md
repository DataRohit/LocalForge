# 31: User registration and profile endpoints

**What to build:** `/users/` and `/users/me/` — anyone can register, an authenticated caller can read and update
their own account, and nobody can read or modify anyone else's.

**Blocked by:** 29, 30.

**Status:** ready-for-agent

- [ ] Registration creates an inactive account and returns the created representation without any sensitive field.
- [ ] Registration requires a password confirmation and applies the configured password validators, returning
      per-field errors in the standard envelope.
- [ ] Registering with an existing username or email returns the same response as a successful registration, so
      accounts cannot be enumerated; the real outcome is communicated only by the email that follows.
- [ ] The profile endpoint returns the authenticated caller's own account and never accepts an identifier that
      would let it return someone else's.
- [ ] The profile endpoint supports partial update of the mutable fields only; the active flag, permissions flags,
      password, and identifier are not writable through it.
- [ ] Deleting the profile requires the current password and is irreversible; the response documents what is
      retained.
- [ ] Listing accounts is unavailable to ordinary callers; a non-staff caller does not receive other accounts.
- [ ] Unauthenticated access to the profile endpoint returns the unauthorized code, not a redirect to a login page.
- [ ] Registration is rate-limited.
- [ ] Integration tests cover registration success, every validation failure, the duplicate cases, profile read,
      partial update, forbidden field update, deletion, and unauthenticated access.
