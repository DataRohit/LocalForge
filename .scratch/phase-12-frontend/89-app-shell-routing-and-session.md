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

**Status:** done (Locally Verified)

- [x] The session is derived from `GET /api/v1/users/me/` and never stored in browser storage; a `401` signs out.
- [x] Navigation items and route guards derive from backend-granted capabilities only.
- [x] `src/session/fragment.ts` reads `account` and `token` from the URL fragment and removes them from the address bar
      and history, and the bootstrap calls it before the first render; it is fully tested and never logs credentials.
- [x] Loading, empty, error, and offline states exist for the shell and are tested.
- [x] Primitives in `ui/` use Tailwind tokens from the `@theme` block, visible focus styles, and semantic elements, with
      one `cn` helper and no `@apply` outside `ui/`.
- [x] Unknown client routes show a not-found screen; the Vite development proxy sends `/api/`, `/ws/`, and
      `/health/` to the backend so a mistyped API path never renders the shell in development.
- [x] Status recorded as Locally Verified.

**Verification (2026-10-10, no runtime change):**

- `make frontend-check` passes: 21 test files, 416 tests, coverage 100% for statements (286), branches (176),
  functions (96), and lines (283); documentation, architecture, bundle, and drift checks clean. The initial bundle is
  118,182 of 200,000 gzip bytes of JavaScript and 3,092 of 25,000 of CSS.
- Session: derived from `GET /api/v1/users/me/` through the query client, with loading, signed-in, signed-out, and
  error states, a refresh and retry on an expired access cookie, and cache clearing when the API client announces
  session loss. The architecture test fails on any use of browser storage.
- Capabilities: navigation and the `RequireCapability` guard read only the capabilities the backend grants; a missing
  capability shows the no-access screen, and a signed-out visitor is redirected to sign-in.
- States: loading (splash), empty (an account with no capabilities), error (session error with retry, warning tone when
  offline), and offline (a persistent notice that follows the browser's online and offline events) are implemented and
  tested for the shell.
- Fragment credentials: `takeFragmentCredentials` is called by the bootstrap before the first render, removes the
  fragment from the current history entry, and the credentials live only in a context that forgets them once used.
- Primitives: `Button`, `TextField`, `Alert`, `Spinner`, `Page`, `Card`, `TextLink`, and the `cn` helper use the `@theme`
  tokens, visible focus styles, and semantic elements; the field links hint and error through `aria-describedby`.
- Unknown routes render the not-found screen; the development proxy test confirms `/api/`, `/health/`, and `/ws/` go
  to the testing backend.
- No runtime, Compose, or service change; `development` was not touched.

**Findings and dispositions:**

- The route paths could not live in `ui/` without breaking the layer rule, so they live in `session/`.
- The architecture test caught a routes test importing the API module; replaced by a mock-backend `disconnect` helper.
- An anonymous visit made three extra requests (seed CSRF, then a refresh that cannot succeed). The backend sets the
  CSRF cookie at sign-in and deletes it with the session, so the client now skips the refresh without it.
- The accessible description of an invalid field ran the word `Error:` into the message; fixed in the markup.
- React Router and Zod carry inert URL strings (a parsing base, a documentation link in a warning, JSON Schema
  identifiers); each is on an explicit allowlist in the bundle check and every other remote URL still fails the build.
- Focus moves to the main region after a route change, not on first render; focus is tested here and re-audited in
  Ticket 94.

Disposition: complete.
