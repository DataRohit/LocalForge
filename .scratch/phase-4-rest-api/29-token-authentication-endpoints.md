# 29: Token authentication endpoints

**What to build:** `/token/login/` and `/token/logout/` — a client exchanges credentials for a token, uses it on
subsequent requests, and destroys it on logout.

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

- [x] Login accepts credentials and returns a token for an active account.
- [x] Logout destroys the caller's token, so reusing it afterwards is rejected.
- [x] An inactive account cannot obtain a token, and the response does not reveal that the account exists but is
      inactive.
- [x] Wrong credentials and an unknown account return the same status, the same body, and take the same time, so
      accounts cannot be enumerated.
- [x] Login is rate-limited per address and per account.
- [x] The token appears only in the login response body; it is never logged, never echoed in another endpoint, and
      never placed in a URL.
- [x] The token scheme is configured as a secondary scheme behind the JSON web token scheme.
- [x] The known caveats from `docs/adr/0017-first-party-account-endpoints.md` are recorded in the view docstring:
      tokens are stored in plaintext as the table key, and there is one non-expiring token per account.
- [x] Integration tests cover success, wrong password, unknown account, inactive account, missing fields, rate
      limiting, logout, and reuse after logout.
- [x] Login admission lives in the authoritative PostgreSQL primary, remains exact across processes, survives
      general-cache clear and eviction, prunes globally expired rows in bounded oldest-first batches, and fails
      closed with correlated `503` on outage or state loss.
- [x] Exact current profiles authenticate normally; recognized lower-iteration PBKDF2 profiles receive Django
      runtime hardening and active-success upgrade. Higher, mixed, malformed, unrecognized, and unsupported
      multi-parameter profiles require reset through the same bounded dummy schedule and generic rejection as an
      unknown account, without verifying or mutating the stored encoding, inside the approved thirty-after-five
      median timing bound.
- [x] Statistical credential timing cases run as an explicit bounded four-worker stage, while deterministic
      equivalent-work and schedule tests remain in the 100% branch-covered core stage.
- [x] Client address buckets use `REMOTE_ADDR` directly and accept forwarded hops only from the explicit trusted
      Traefik subnet; forwarded hops are parsed lazily right-to-left, and the direct published application port is
      bound to host loopback.
