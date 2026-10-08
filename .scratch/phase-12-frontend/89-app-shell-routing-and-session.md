# 89: App shell, routing, and session

**What to build:** The application shell, route table with guards, session bootstrap from the profile endpoint,
backend-capability-driven navigation, and the reusable UI primitives.

**Blocked by:**

- [88: API client and query layer](88-api-client-and-query-layer.md)

**Governing sources:**

- [Frontend standard](../../docs/platform/frontend-standard.md)
- [Browser authentication ADR](../../docs/adr/0025-browser-authentication.md)
- [Ticket index](../README.md)

**Status:** pending

- [ ] The session is derived from `GET /api/v1/users/me/` and never stored in browser storage.
- [ ] Navigation items and route guards derive from backend-granted capabilities only.
- [ ] Loading, empty, error, and offline states exist for the shell and are tested.
- [ ] Primitives in `ui/` use Tailwind tokens, visible focus styles, and semantic elements.
- [ ] Unknown client routes show a not-found screen; unknown `/api/` paths are never served the SPA shell.
- [ ] Status recorded as Locally Verified.
