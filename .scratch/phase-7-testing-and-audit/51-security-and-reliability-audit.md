# 51: Security and reliability audit

**What to build:** a deliberate pass over the finished platform looking for the failures that only show up under
adversarial or degraded conditions, with every finding either fixed or recorded as an accepted risk.

**Blocked by:** 49.

**Status:** ready-for-agent

- [ ] The framework's own deployment checks run clean in the production-shaped configuration, and every silenced
      check is justified in writing.
- [ ] No credential, token, or key appears in the repository, in an image layer, in a log line, or in an error
      response. The secret-scanning hook passes over the full history of this work.
- [ ] No vendor default account survives in any service: the broker lists only the configured user and no `guest`,
      and every dashboard rejects an unauthenticated request before serving any page.
- [ ] Debug mode is off outside development, and an error response never returns a traceback or settings detail.
- [ ] Security headers are set: content type options, frame options, referrer policy, and a content security
      policy. Transport security settings are correct for the deployed shape and documented as inert on a local
      plaintext network.
- [ ] Authentication endpoints are rate-limited, and the limits are proven by test.
- [ ] User enumeration is not possible through registration, password reset, username reset, or login: response
      status, body, and timing do not distinguish an existing account from a missing one.
- [ ] Password validation enforces the configured policy, and the hashing algorithm is the memory-hard one chosen
      in settings.
- [ ] Tokens expire as configured, refresh rotation and revocation behave as documented, and a revoked credential
      is rejected by both the API and the WebSocket path.
- [ ] Object storage grants no anonymous read, and an uploaded file is not reachable without authorisation.
- [ ] Every internal network is unable to reach the internet, proven by command.
- [ ] Dependency and image vulnerability scans run, and every finding is fixed, pinned past, or recorded with a
      reason.
- [ ] Degraded-mode behaviour is exercised: with each backing service stopped in turn, the application returns a
      sensible status rather than hanging or crashing, and recovers when the service returns.
- [ ] Restart resilience is exercised: the whole stack is stopped and started, and it comes back healthy with data
      intact.
- [ ] Findings are recorded, with fixes applied and accepted risks written into the relevant decision record.
