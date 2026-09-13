# 37: Offline API documentation UIs

**What to build:** Swagger UI and ReDoc served from the application itself, working with no internet connection,
so a developer can read and exercise the API from a browser on an air-gapped machine.

**Blocked by:** 36.

**Status:** ready-for-agent

- [ ] The schema endpoint, Swagger UI, and ReDoc are the only three routes added, and they document an API whose
      application surface is exactly the documented one.
- [ ] Static assets for both UIs are served from the local sidecar package, not a content delivery network.
      Verified by loading both pages with the machine offline: they render fully.
- [ ] The sidecar package is listed in the installed applications, without which the assets are not collected.
- [ ] Both UIs are reachable at the paths in `docs/platform/service-inventory.md`.
- [ ] Swagger UI can authenticate with both schemes and successfully call an authenticated endpoint.
- [ ] The UIs are available in development and excluded from the testing environment, which runs headless.
- [ ] Exposure of the schema and UIs is controlled by an environment flag, so they can be turned off without a code
      change.
- [ ] A test asserts the schema endpoint returns a valid document and that both UI routes return success.
