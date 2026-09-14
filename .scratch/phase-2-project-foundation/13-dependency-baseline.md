# 13: Dependency baseline

**What to build:** every runtime and development dependency the platform needs, resolved and locked against the
project's Python version, so later tickets never stop to argue about a package.

**Blocked by:** 01.

**Status:** ready-for-agent

- [ ] Dependencies are added with the project's package manager into the correct groups; the lockfile is updated
      and committed. No requirements file is created.
- [ ] The S3 client is moved into the runtime group. Ticket 08 added it to the development group early, because
      the seeding step in `docs/platform/conventions.md` Section 4.7 is a phase-1 deliverable that signs S3
      requests, and that step had no client until then.
- [ ] The runtime group covers the ASGI server with WebSocket support, the REST framework, schema generation with
      its offline asset package, Channels and its channel layer backend, the task queue and its scheduler, the
      database driver, the S3 client, environment parsing, health checks, and metrics instrumentation.
- [ ] The development group covers the existing test tooling plus async test support for consumers.
- [ ] The async interface library floor matches what the current Django release requires, which is higher than what
      Channels alone asks for.
- [ ] Every dependency whose published artifact predates its upstream support for this Python or Django version is
      identified before pinning, and the situation recorded in `docs/adr/0016-accept-release-lag.md`.
- [ ] Resolution succeeds against the configured package index without disabling certificate verification.
- [ ] The schema generator's offline asset package is present, because the platform has no internet at runtime and
      both documentation UIs would otherwise render blank.
- [ ] Importing every new top-level package succeeds under the project interpreter.
- [ ] `uv run poe check` is green.
