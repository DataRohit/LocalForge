# 29: Token authentication endpoints

**What to build:** `/token/login/` and `/token/logout/` — a client exchanges credentials for a token, uses it on
subsequent requests, and destroys it on logout.

**Blocked by:** 27, 18.

**Status:** ready-for-agent

- [ ] Login accepts credentials and returns a token for an active account.
- [ ] Logout destroys the caller's token, so reusing it afterwards is rejected.
- [ ] An inactive account cannot obtain a token, and the response does not reveal that the account exists but is
      inactive.
- [ ] Wrong credentials and an unknown account return the same status, the same body, and take the same time, so
      accounts cannot be enumerated.
- [ ] Login is rate-limited per address and per account.
- [ ] The token appears only in the login response body; it is never logged, never echoed in another endpoint, and
      never placed in a URL.
- [ ] The token scheme is configured as a secondary scheme behind the JSON web token scheme.
- [ ] The known caveats from `docs/adr/0017-first-party-account-endpoints.md` are recorded in the view docstring:
      tokens are stored in plaintext as the table key, and there is one non-expiring token per account.
- [ ] Integration tests cover success, wrong password, unknown account, inactive account, missing fields, rate
      limiting, logout, and reuse after logout.
