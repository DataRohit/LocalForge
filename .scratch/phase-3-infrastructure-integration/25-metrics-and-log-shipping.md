# 25: Application metrics and log shipping

**What to build:** the application's own request metrics scraped alongside the infrastructure metrics, and its logs
appearing in the log store with labels that identify the service, so a developer can correlate a slow request with
its log line.

**Blocked by:** 11, 17.

**Status:** done

- [x] Request and database instrumentation is enabled and exposes a metrics endpoint on the application.
- [x] The metrics endpoint is scraped and its target reports healthy.
- [x] The metrics endpoint is not reachable from the edge network; it is internal only.
- [x] Request counts, latency histograms, and response status codes are visible in the visualization UI on a
      provisioned dashboard.
- [x] Application logs are emitted as structured records to stdout, including a request identifier.
- [x] A request identifier is generated per request, attached to every log record for that request, and returned
      in a response header so a user-reported failure can be traced.
- [x] Logs reach the log store and are queryable by service label within thirty seconds.
- [x] Log records never contain credentials, tokens, session keys, or password fields; a test asserts that a login
      attempt logs no password.
- [x] The log level is set from the environment.
