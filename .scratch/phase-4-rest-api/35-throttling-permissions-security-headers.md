# 35: Throttling, permissions, and security headers

**What to build:** the cross-cutting protections every route inherits — rate limits that actually stop abuse,
permissions that default to closed, and the response headers a browser needs to defend the client.

**Blocked by:** 31.

**Status:** done

- [x] Throttle rates are configured per scope from the environment: a tight scope for authentication and recovery
      endpoints, a looser one for authenticated reads, and an anonymous scope.
- [x] Rate-limited responses return the too-many-requests code in the standard envelope, with a `Retry-After`
      header.
- [x] Throttle state is shared across application instances rather than per process. General reusable scopes live
      in cache; Ticket 29's security admission remains in the authoritative PostgreSQL primary and is not moved.
- [x] Throttling keys on both the client address and the account, so one abusive client cannot lock out an entire
      shared address, and one account cannot evade the limit by changing address.
- [x] Identified requests use account plus `(address, account)` composite dimensions in one all-or-nothing Valkey
      decision; unidentified requests use address only, and denied requests increment no dimension.
- [x] A broad address admission runs at the outer ASGI HTTP boundary before declared or streamed 413 handling,
      CORS preflight, negotiation, parsing, authentication, and permissions without replacing any endpoint's
      authoritative PostgreSQL admission.
- [x] The ASGI boundary derives the trusted address from transport scope and runs synchronous Valkey admission
      outside the event loop; timeout and cache failure retain the documented fail-open behavior.
- [x] General identity prefers authenticated `user.pk`, then the signed subject from a view-declared `token` or
      `refresh` field; undeclared raw account fields are rejected and never select a bucket.
- [x] The proxy sets the forwarded-address header and the application trusts it only from the proxy, so a client
      cannot spoof its address to escape throttling.
- [x] Permissions default to denying unauthenticated access; each public endpoint opts out explicitly and its
      reason is in its docstring.
- [x] Object-level permission is enforced for anything addressable, so changing an identifier in a URL cannot reach
      another account's data.
- [x] Security headers are set: content-type options, frame options, referrer policy, and a content security
      policy.
- [x] Transport security settings are correct for the deployed shape, and the fact that they are inert on a local
      plaintext network is documented rather than silently disabled.
- [x] Cross-origin rules allow only the configured origins, from the environment. A wildcard origin is not used
      with credentials.
- [x] Preflight applies only to resolvable versioned API routes and advertises that operation's methods; every
      supplied origin varies ordinary, 413, and 429 responses, while duplicate origins are never reflected.
- [x] Integration tests prove each throttle scope trips at its limit and recovers, and assert every header is
      present.
