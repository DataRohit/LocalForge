# 12: Database administration dashboard

**What to build:** a browser UI where a developer can inspect both database nodes without installing a client, with
both servers already registered on first launch.

**Blocked by:** 04.

**Status:** ready-for-agent

- [ ] The dashboard runs with the registry name, its own named volume, and is reachable from the host.
- [ ] It binds an address that works in an IPv4-only setup rather than relying on the default.
- [ ] The bundled mail server is disabled, since mail capture is handled elsewhere.
- [ ] Both the primary and the standby are pre-registered from a mounted server definition file.
- [ ] Server definitions are applied on every launch, so the registration is declarative rather than a one-time
      side effect of first boot.
- [ ] Login requires the generated credential.
- [ ] The dashboard sits on the data network only and is excluded from the testing environment.
