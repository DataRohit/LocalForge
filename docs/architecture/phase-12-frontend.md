# Phase 12: Frontend

Authoritative for: the client route map, the project layout and commands, the quality bars, the browser support
matrix, and the delivery gate. Plan locked **2026-10-09**: a change to anything below needs an ADR update and the
user's approval.

Decisions behind it: [0026](../adr/0026-react-and-tailwind-frontend.md),
[0027](../adr/0027-frontend-hosting-and-edge-routing.md), [0028](../adr/0028-frontend-toolchain.md). Working rules:
[frontend-standard.md](../platform/frontend-standard.md). Tickets: 87–99 in `.scratch/phase-12-frontend/`. It starts
only after the user accepts the Phase 11 freeze.

## 1. Scope

A React and Tailwind CSS single-page application for the frozen backend: registration, activation, sign-in and
sign-out, profile, password and username recovery and change, and live notifications. It is built, tested, and
packaged as a pinned container with an inert edge candidate. Nothing is deployed to `development` without the
user's authorization.

Out of scope: server-side rendering, a backend-for-frontend, internationalization, dark mode, a service worker or
installable app, analytics or error-reporting services, an admin interface, notification history, file upload,
and third-party sign-in.

## 2. Client route map

| Path | Access | Backend contract |
| --- | --- | --- |
| `/` | any | Redirects to `/account` when signed in, otherwise `/sign-in` |
| `/sign-in` | public | `browser/csrf/`, `browser/login/` |
| `/register` | public | `users/` (POST) |
| `/activate` | public | Reads `account` and `token` from the fragment, then `users/` (POST, activation branch) |
| `/resend-activation` | public | `users/resend_activation/` |
| `/forgot-password` | public | `users/reset_password/` |
| `/reset-password` | public | Fragment credentials, `users/reset_password_confirm/` |
| `/forgot-username` | public | `users/reset_username/` |
| `/reset-username` | public | Fragment credentials, `users/reset_username_confirm/` |
| `/account` | signed in | `users/me/` GET, PATCH, DELETE |
| `/account/password` | signed in | `users/set_password/` |
| `/account/username` | signed in | `users/set_username/` |
| `/notifications` | signed in | `/ws/notifications/` |
| any other path | any | Not-found screen |

A protected route renders only when `capabilities` from `users/me/` grants it. Fragment credentials are removed
from the address bar and history on load, before anything else renders.

## 3. Project and commands

The project lives in `frontend/` with its own `package.json` (exact pins), `package-lock.json`, `.node-version`,
and `tsconfig`. The source layout is [the frontend standard](../platform/frontend-standard.md) Section 2. There are
no `VITE_*` or other build-time variables: every URL is relative, so one build serves every environment.

| Make target | Runs |
| --- | --- |
| `frontend-install` | `npm ci` |
| `frontend-format`, `frontend-lint`, `frontend-types` | Prettier check, ESLint with zero warnings, `tsc --noEmit` |
| `frontend-docs-standard` | The TSDoc checker |
| `frontend-test` | Vitest with coverage thresholds, parallel |
| `frontend-build` | Production build and the bundle checks |
| `frontend-check` | All of the above in order |
| `frontend-e2e` | Playwright against the `testing` backend |
| `frontend-image` | The pinned container build |
| `frontend-audit` | Licence, advisory, and install-script audit |

The Vite development server proxies `/api/` and `/ws/` to a backend started in host mode against `testing`. It
never targets `development`.

## 4. Quality bars

Same posture as the backend: enforced, never lowered to pass.

| Bar | Value |
| --- | --- |
| Coverage | **100%** statements, branches, functions, and lines. The only exclusions are generated API types and the bootstrap `main.tsx`; each is listed in the coverage configuration with its reason |
| Type safety | `strict`, no `any`, no unchecked non-null assertion |
| Lint | Zero ESLint warnings |
| Accessibility | Zero axe violations in loading, empty, error, and populated states |
| Test hygiene | Per-test timeout; `console.error` and `console.warn` fail a test; parallel execution |
| Bundle budget | Initial JavaScript at most 200 kB gzip, CSS at most 25 kB gzip, checked in the build |
| Remote URLs | None in the built output |

## 5. Browser support

| Browser | Support |
| --- | --- |
| Chromium and Firefox, current and previous stable | Supported and tested by Playwright |
| WebKit, current stable | Supported and tested on HTTPS origins; unsupported on plain-HTTP local origins because of `Secure` cookies |
| Older or other engines | Unsupported |

## 6. Delivery

- **Image.** `docker/frontend/Dockerfile`: digest-pinned Node build stage, then digest-pinned `nginx-unprivileged`,
  non-root, read-only filesystem, no package manager, no secret, no volume.
- **Caching.** Hashed files under `/assets/` are immutable for a year; `index.html` is `no-cache`.
- **Headers.** Strict CSP (`default-src 'self'`, no inline script, no `unsafe-eval`), `Referrer-Policy: no-referrer`,
  `X-Content-Type-Options: nosniff`, `frame-ancestors 'none'`.
- **Health.** nginx answers `/healthz` on its internal port; Traefik's service health check uses the same path.
- **Logs.** nginx writes structured access and error logs to stdout. The existing pipeline is expected to collect
  them; Ticket 97 confirms this without reading live-mounted configuration unless the user authorizes it, and
  otherwise supplies a candidate patch.
- **Rollback.** Reverting the routing candidate returns every path to Django; the SPA is stateless, so no data is
  involved.

## 7. Gate

1. Every ticket 87–99 has a complete verification record.
2. `make frontend-check`, `make frontend-e2e`, and `make frontend-audit` pass, and the backend `testing` gate still
   passes in container and host mode.
3. The container runs standalone with its health check passing and a clean bounded log window.
4. The routing candidate, deployment procedure, and rehearsed rollback are in `docs/runbooks/frontend-deployment/`.
5. `docs/handover/phase-12.md` records ownership, commands, evidence, and the unverified production gates.
6. Nothing has been applied to `localforge-dev`. Deployment waits for the user's explicit authorization.
