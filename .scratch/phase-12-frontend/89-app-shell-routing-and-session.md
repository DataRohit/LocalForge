# 89: App shell, routing, and session

**What to build:** The application shell, route table with guards, session bootstrap from the profile endpoint, backend-
capability-driven navigation, the fragment-credential reader, and the reusable UI primitives.

**Blocked by:**

- [88: API client and query layer](88-api-client-and-query-layer.md)

**Governing sources:**

- [Frontend standard](../../docs/platform/frontend-standard.md)
- [Browser authentication ADR](../../docs/adr/0025-browser-authentication.md)
- [Phase 12 replan record](../../docs/architecture/phase-12-frontend.md)
- [Ticket index](../README.md)

**Status:** pending

- [ ] The session is derived from `GET /api/v1/users/me/` and never stored in browser storage; a `401` signs out.
- [ ] Navigation items and route guards derive from backend-granted capabilities only.
- [ ] `src/session/fragment.ts` reads `account` and `token` from the URL fragment and removes them from the address bar
      and history, and the bootstrap calls it before the first render; it is fully tested and never logs credentials.
- [ ] Loading, empty, error, and offline states exist for the shell and are tested.
- [ ] Primitives in `ui/` use Tailwind tokens from the `@theme` block, visible focus styles, and semantic elements, with
      one `cn` helper and no `@apply` outside `ui/`.
- [ ] Unknown client routes show a not-found screen; the Vite development proxy sends `/api/`, `/ws/`, and
      `/health/` to the backend so a mistyped API path never renders the shell in development.
- [ ] Status recorded as Locally Verified.
