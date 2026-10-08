# Frontend standard

Authoritative for: how `frontend/` code is structured, styled, tested, and allowed to talk to the backend.

Decisions behind it: [0024](../adr/0024-full-stack-pivot.md), [0025](../adr/0025-browser-authentication.md),
[0026](../adr/0026-react-and-tailwind-frontend.md), [0027](../adr/0027-frontend-hosting-and-edge-routing.md),
[0028](../adr/0028-frontend-toolchain.md). The backend contract it consumes is
[openapi-v1.yaml](../api/openapi-v1.yaml) and [websocket-v1.md](../api/websocket-v1.md).

## 1. The rule in one line

The browser is untrusted and the backend is the authority: the frontend renders backend state, never decides it.

## 2. Layout

| Directory under `frontend/src/` | Owns | May import from |
| --- | --- | --- |
| `ui/` | Reusable presentational components, no data access | `ui/` only |
| `features/<name>/` | One workflow: its screens, hooks, and form schemas | `ui/`, `api/`, `session/` |
| `api/` | The only module that calls `fetch` or opens a socket | nothing in the app |
| `session/` | Who is signed in and what the backend grants them | `api/` |
| `routes/` | Route table and guards | `features/`, `session/` |

Imports run one way, from the bottom of the table up. A test enforces the direction, as
`backend/tests/unit/test_architecture.py` does for the backend.

## 3. Components

- One component per file, named for what it is. Props are a typed object; no prop spreading into DOM elements.
- Components render; hooks fetch. A component never calls `fetch`.
- Effects exist only to synchronize with something outside React (a socket, a timer, the document title) and always
  return their cleanup. Derived values are computed during render, not stored.
- No `dangerouslySetInnerHTML`. Backend text is rendered as text.

## 4. Tailwind

- Utility classes are the primary styling. Design tokens (colour, spacing, type scale, radius) live in the Tailwind
  `@theme` block and nowhere else; no arbitrary values where a token exists.
- Compose conditional classes through one `cn` helper. Use `@apply` only inside `ui/` primitives, never in features.
- Mobile-first: unprefixed classes are the narrow layout, breakpoint prefixes widen it.
- Every interactive element has a visible `focus-visible` style. Colour is never the only carrier of meaning.
- Fonts are system fonts or bundled files. No stylesheet, font, or script is loaded from another origin.

## 5. Accessibility

- Semantic elements first: `button` for actions, `a` for navigation, real `label` for every input, landmarks for
  page regions, one `h1` per page.
- Everything is reachable and operable by keyboard in a logical order; focus moves to the new context after a route
  change or dialog open and returns when it closes.
- Errors and notifications announce through an `aria-live` region. Form errors attach to their field.
- The accessibility suite reports zero axe violations for every screen in the loading, empty, error, and populated
  states.

## 6. State ownership

| State | Owner |
| --- | --- |
| Data fetched from the backend | TanStack Query, keyed by resource |
| Who is signed in, granted capabilities | `session/`, derived from `GET /api/v1/users/me/` |
| Form fields and local validation | React Hook Form with Zod schemas |
| Open dialog, selected tab, other view-only state | `useState` in the component that renders it |
| Current screen and filters | The URL |
| Notifications received this connection | The socket hook in `features/notifications/` |

There is no global client store. Credentials are never held in JavaScript: the cookies are `HttpOnly`, so the
frontend has nothing to store and nothing to leak. Nothing sensitive goes in `localStorage`, `sessionStorage`, a URL,
a log line, or an error report.

## 7. API access

- `api/` is the single client. It sends `credentials: "same-origin"`, adds `X-CSRFToken` to unsafe requests, and
  parses every non-2xx body as the error envelope from [0018](../adr/0018-api-error-contract.md). All URLs are
  relative and there are no build-time environment variables.
- Credentials arrive in the URL fragment of emailed links. The route reads them once, removes them from the address
  bar and history before anything else renders, and never logs or stores them.
- Request and response types derive from `openapi-v1.yaml`. A schema change that is not reflected in the types fails
  the type check.
- Every status code a route documents has a handled outcome in the caller: a message, a redirect, a retry, or a
  sign-out. A code the schema does not list is treated as an unexpected failure, never ignored.
- A `401` or an expired session clears session state and routes to sign-in without a retry loop.
- The socket client follows the close-code table in `websocket-v1.md`: refresh and reconnect for `4403`, bounded
  backoff for `4500`, stop for the codes that say stop.

## 8. Required states

Every data-driven screen implements and tests four states: **loading**, **empty**, **error**, and **offline**.
Offline is detected from the browser and from failed requests; it shows a persistent notice and does not discard
user input.

## 9. Documentation

No comments, matching [0020](../adr/0020-no-comments-structured-docstrings.md). Every module, component, hook, and
exported function carries a TSDoc block with a one-line title, a two to three line description, its parameters or
props, what it returns, and what it throws. ESLint and a checker in the frontend quality gate enforce it.

## 10. Quality gate

`make frontend-check` runs, in order: format check, ESLint (type-aware, zero warnings), `tsc --noEmit`, the TSDoc
checker, the import-direction test, Vitest, and the production build. Browser end-to-end and accessibility suites
run through `make frontend-e2e` against the `testing` backend. A gate is never weakened to pass.

The numeric bars match the backend's posture and are fixed in
[the Phase 12 architecture](../architecture/phase-12-frontend.md) Section 4: **100%** statements, branches,
functions, and lines; zero lint warnings; zero axe violations; a bundle budget of 200 kB gzip JavaScript and 25 kB
gzip CSS. Tests fail on `console.error` and `console.warn`, run in parallel, and carry timeouts. Supported
browsers are listed in Section 5 of the same document.

## 11. Security

- The built SPA references only its own origin. A test fails the build if a bundled file contains an absolute URL
  to another host.
- The `Content-Security-Policy` is `default-src 'self'` with no inline script and no `unsafe-eval`.
- Dependencies are exact-pinned, installed with `npm ci`, and audited for licence, advisories, and install scripts.
