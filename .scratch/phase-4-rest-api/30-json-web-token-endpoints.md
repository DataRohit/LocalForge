# 30: JSON web token endpoints

**What to build:** `/jwt/create/`, `/jwt/refresh/`, and `/jwt/verify/` — the primary authentication scheme, issuing
short-lived access tokens and longer-lived refresh tokens that can be rotated and revoked.

**Blocked by:**

- [27](27-api-foundation-and-error-envelope.md)
- [18](../phase-2-project-foundation/18-custom-user-model.md)

**Governing sources:**

- [Root instructions](../../AGENTS.md)
- [Build plan](../../docs/build/plan.md)
- [First-party account endpoints ADR](../../docs/adr/0017-first-party-account-endpoints.md)
- [API error contract ADR](../../docs/adr/0018-api-error-contract.md)
- [Platform conventions](../../docs/platform/conventions.md)

**Status:** done

- [x] Create returns an access token and a refresh token for valid credentials on an active account.
- [x] Refresh exchanges a valid refresh token for a new access token.
- [x] Verify reports whether a token is valid without returning its contents.
- [x] Access token lifetime is short and refresh lifetime is longer; both come from the environment.
- [x] Refresh tokens rotate on use, and the previous refresh token is blacklisted so a stolen one cannot be
      replayed.
- [x] Signing uses the symmetric algorithm with a dedicated signing key from the environment, distinct from the
      framework's general-purpose secret. The asymmetric signing extra is not installed, since it is unused.
- [x] An expired, malformed, revoked, or blacklisted token is rejected with the unauthorized code and the standard
      envelope.
- [x] Credential failures are indistinguishable between a wrong password and an unknown account.
- [x] Token claims carry no personal data beyond the user identifier.
- [x] The library's release-lag status from `docs/adr/0016-accept-release-lag.md` is checked before pinning; if the
      unreleased breaking change that turns a not-found into an unauthorized is pulled in, the documented contract
      is updated to match.
- [x] Integration tests cover create, refresh, rotation, verify for valid and invalid tokens, expiry using frozen
      time, blacklist after rotation, and use of a token belonging to a deleted account.
- [x] SimpleJWT's upstream `flushexpiredtokens` command is integration-tested against the authoritative primary:
      expired outstanding and cascaded blacklist rows are removed while unexpired rows remain. Daily execution is
      an explicit [Ticket 43](../phase-6-async-services/43-periodic-task-scheduler.md) scheduler responsibility and
      is deliberately not scheduled by [Ticket 30](30-json-web-token-endpoints.md).
