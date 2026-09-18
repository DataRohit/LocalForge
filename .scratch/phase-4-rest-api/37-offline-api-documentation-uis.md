# 37: Offline API documentation UIs

**What to build:** Swagger UI and ReDoc served from the application itself, working with no internet connection,
so a developer can read and exercise the API from a browser on an air-gapped machine.

**Blocked by:** 36.

**Status:** done

- [x] The schema endpoint, Swagger UI, and ReDoc are the only three routes added, and they document an API whose
      application surface is exactly the documented one. Sidecar `/static/` URLs are ASGI-served infrastructure
      resources, not additions to the Django application route table.
- [x] Static assets for both UIs are served from the local sidecar package, not a content delivery network.
      Verified through the deployed `uvicorn config.asgi:application --app-dir src` path with all five assets,
      content types, conditional caching, unknown resources, and path traversal exercised over loopback.
- [x] The sidecar package is listed in the installed applications, without which the assets are not collected.
- [x] Both UIs are reachable at the paths in `docs/platform/service-inventory.md`.
- [x] Swagger UI can authenticate with both schemes and successfully call an authenticated endpoint.
- [x] The UIs are available in development and excluded from the testing environment, which runs headless.
- [x] Exposure of the schema and UIs is controlled by an environment flag, so they can be turned off without a code
      change; the same flag disables ASGI static interception.
- [x] Tests assert the schema endpoint returns a valid document, both UI routes return success, direct generation is
      warning-free with documentation enabled, and the committed fixed-contract artifact remains deterministic.
