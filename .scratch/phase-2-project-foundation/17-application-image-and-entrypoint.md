# 17: Application container image and entrypoint

**What to build:** a reproducible image that runs the Django application, and a second stage that runs the test
suite. Starting the container waits for its dependencies, applies migrations, collects static files, and serves
under the ASGI server — in that order, with the port opening last.

**Blocked by:** 14.

**Status:** done

- [x] The image is multi-stage with a runtime stage and a test stage, both from the same pinned base matching the
      project's Python version. No floating tag is used.
- [x] Dependencies install from the lockfile so the build is reproducible, and layer ordering caches them
      separately from source.
- [x] The container runs as a non-root user and the application directory is not writable by it.
- [x] The entrypoint blocks until every declared dependency answers a real readiness probe, not a socket connect,
      and fails with a message naming the service when the timeout expires.
- [x] Migrations run in the entrypoint before the port is bound, so anything waiting on this service being healthy
      is also waiting on a current schema.
- [x] Static files are collected during startup or build, into the volume from the registry.
- [x] The ASGI server runs with WebSocket support enabled and a worker count from the environment.
- [x] The container exposes a health endpoint that the platform's health check uses.
- [x] Signals are handled so the container stops promptly rather than being killed after a timeout.
- [x] Only the worker and scheduler images reuse this build; none of them runs migrations.
- [x] The image builds offline after the first build, and the built image is recorded in
      `docs/platform/service-inventory.md`.
