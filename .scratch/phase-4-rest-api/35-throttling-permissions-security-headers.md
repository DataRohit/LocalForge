# 35: Throttling, permissions, and security headers

**What to build:** the cross-cutting protections every route inherits — rate limits that actually stop abuse,
permissions that default to closed, and the response headers a browser needs to defend the client.

**Blocked by:** 31.

**Status:** ready-for-agent

- [ ] Throttle rates are configured per scope from the environment: a tight scope for authentication and recovery
      endpoints, a looser one for authenticated reads, and an anonymous scope.
- [ ] Rate-limited responses return the too-many-requests code in the standard envelope, with a `Retry-After`
      header.
- [ ] Throttle state is shared across application instances rather than per process. General reusable scopes live
      in cache; Ticket 29's security admission remains in the authoritative PostgreSQL primary and is not moved.
- [ ] Throttling keys on both the client address and the account, so one abusive client cannot lock out an entire
      shared address, and one account cannot evade the limit by changing address.
- [ ] The proxy sets the forwarded-address header and the application trusts it only from the proxy, so a client
      cannot spoof its address to escape throttling.
- [ ] Permissions default to denying unauthenticated access; each public endpoint opts out explicitly and its
      reason is in its docstring.
- [ ] Object-level permission is enforced for anything addressable, so changing an identifier in a URL cannot reach
      another account's data.
- [ ] Security headers are set: content-type options, frame options, referrer policy, and a content security
      policy.
- [ ] Transport security settings are correct for the deployed shape, and the fact that they are inert on a local
      plaintext network is documented rather than silently disabled.
- [ ] Cross-origin rules allow only the configured origins, from the environment. A wildcard origin is not used
      with credentials.
- [ ] Integration tests prove each throttle scope trips at its limit and recovers, and assert every header is
      present.
