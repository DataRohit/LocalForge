# 97: Frontend container and edge candidate

**What to build:** The pinned frontend image and the inert candidate Compose and Traefik changes that would serve it
through the existing edge, ready for deployment on the user's authorization.

**Blocked by:**

- [95: Frontend unit and integration tests](95-frontend-unit-and-integration-tests.md)

**Governing sources:**

- [Frontend hosting and edge routing ADR](../../docs/adr/0027-frontend-hosting-and-edge-routing.md)
- [Conventions: the ID registry](../../docs/platform/conventions.md)
- [Service inventory](../../docs/platform/service-inventory.md)
- [Phase 12 replan record](../../docs/architecture/phase-12-frontend.md)
- [Ticket index](../README.md)

**Status:** pending

- [ ] The Dockerfile uses digest-pinned base images, enables the pinned pnpm through Corepack, installs with
      `pnpm install --frozen-lockfile`, and its final stage is non-root, read-only, and has no package manager.
- [ ] The image is built and tested standalone outside both Compose projects; no `localforge-dev` object is touched.
- [ ] A candidate overlay and Traefik labels implement the routing table in the ADR, and are not applied to
      `compose.development.yaml`.
- [ ] `/healthz`, the security headers, the immutable-asset and `no-cache` rules, and the SPA fallback are tested
      against the running image, and the Traefik service health check in the candidate uses `/healthz`.
- [ ] An unknown `/api/` path in the candidate routing still reaches Django, never the SPA shell.
- [ ] Collection of the frontend container's logs by the existing pipeline is confirmed, or a candidate patch is
      supplied; mounted collector configuration is not read without the user's authorization.
- [ ] The registry, service inventory, Kubernetes mapping, and convention audit rows for `frontend-fe6vn` agree.
- [ ] Status recorded as Production-Ready only if every item above passes; otherwise Implemented.
