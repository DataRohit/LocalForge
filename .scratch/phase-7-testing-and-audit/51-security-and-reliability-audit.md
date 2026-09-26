# 51: Security and reliability audit

**What to build:** a deliberate pass over the finished platform looking for the failures that only show up under
adversarial or degraded conditions, with every finding either fixed or recorded as an accepted risk.

**Blocked by:**

- [49](49-dual-mode-test-execution.md)

**Governing sources:**

- [Root instructions](../../AGENTS.md)
- [Ticket index and phase gates](../README.md)
- [Build plan](../../docs/build/plan.md)
- [Platform conventions](../../docs/platform/conventions.md)
- [Service inventory](../../docs/platform/service-inventory.md)
- [Documentation standard](../../docs/platform/documentation-standard.md)

**Status:** done

**Reopened 2026-09-26:** a clean Docker rehearsal reproduced a Windows-only registration timing
failure after the container suite. The timing test executed eager Celery delivery inside the HTTP
measurement, unlike the public RabbitMQ publication boundary. Completion now requires an isolated
real-broker timing path and five consecutive independent host passes.

**Reverified 2026-09-26:** registration timing now publishes to isolated real RabbitMQ queues with
eager execution disabled and no worker. The complete host timing stage passed under four workers,
then five independent host processes passed with health, ownership, residue, and bounded-log audits.

**Reverified 2026-09-23:** image setup rebuilds local tags from current source, audit failures identify the exact
image boundary, and the refreshed immutable image snapshot passes the complete security audit.

- [x] The framework's own deployment checks run clean in the production-shaped configuration, and every silenced
      check is justified in writing.
- [x] No credential, token, or key appears in the repository, in an image layer, in a log line, or in an error
      response. The secret-scanning hook passes over the full history of this work.
- [x] No vendor default account survives in any service: the broker lists only the configured user and no `guest`,
      and every dashboard rejects an unauthenticated request before serving any page.
- [x] Socket exposure is accounted for: a read-only bind of the Docker socket does not make the API read-only, so
      `traefik-tk2jp` and `alloy-al6wz` are either accepted as root-equivalent in writing, or put behind a
      filtering socket proxy added to the registry first.
- [x] The proxy's own surface is reviewed: it runs as root, and `/ping` answers unauthenticated on the published
      dashboard port. Each is either closed or accepted in writing.
- [x] Debug mode is off outside development, and an error response never returns a traceback or settings detail.
- [x] Security headers are set: content type options, frame options, referrer policy, and a content security
      policy. Transport security settings are correct for the deployed shape and documented as inert on a local
      plaintext network.
- [x] Authentication endpoints are rate-limited, and the limits are proven by test.
- [x] User enumeration is not possible through registration, password reset, username reset, or login: response
      status, body, and timing do not distinguish an existing account from a missing one.
- [x] Password validation enforces the configured policy, and the hashing algorithm is the memory-hard one chosen
      in settings.
- [x] Tokens expire as configured, refresh rotation and revocation behave as documented, and a revoked credential
      is rejected by both the API and the WebSocket path.
- [x] Object storage grants no anonymous read, and an uploaded file is not reachable without authorisation.
- [x] Every internal network is unable to reach the internet, proven by command.
- [x] Dependency and image vulnerability scans run, and every finding is fixed, pinned past, or recorded with a
      reason.
- [x] Degraded-mode behaviour is exercised: with each backing service stopped in turn, the application returns a
      sensible status rather than hanging or crashing, and recovers when the service returns.
- [x] Restart resilience is exercised: the whole stack is stopped and started, and it comes back healthy with data
      intact.
- [x] An idle soak and each degraded or recovery exercise include bounded container-log review. Every
      warning-or-higher record is fixed or documented as an accepted risk; successful HTTP, task, scheduler, and
      WebSocket outcomes are never represented by failure-level terminal logs.
- [x] Findings are recorded, with fixes applied and accepted risks written into the relevant decision record.
