# Phase 12: Frontend

Authoritative for: the client route map, the project layout and commands, the quality bars, the browser support
matrix, and the delivery gate. Plan locked **2026-10-09**, replanned **2026-10-10** with the user's approval (Section
8): a further change to anything below needs an ADR update and the user's approval.

Decisions behind it: [0026](../adr/0026-react-and-tailwind-frontend.md),
[0027](../adr/0027-frontend-hosting-and-edge-routing.md), [0028](../adr/0028-frontend-toolchain.md). Working rules:
[frontend-standard.md](../platform/frontend-standard.md). Tickets: 87–99 in `.scratch/phase-12-frontend/`. The user
accepted the Phase 11 freeze (delegated, recorded in `docs/handover/phase-11.md` Section 8) and instructed Phase 12 to
start on 2026-10-10.

Status: tickets 87–99 are **Locally Verified** as of 2026-10-12 and the Gate 4 report is in
[the Phase 12 handover](../handover/phase-12.md). Deployment into `development` has not been applied and waits
for the user's explicit authorization.

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

The project lives in `frontend/` with its own `package.json` (exact pins, `packageManager: pnpm@11.21.0`, licence
`MIT`), `pnpm-lock.yaml`, `pnpm-workspace.yaml` (release-age gate and the one peer override), `.node-version`, and
`tsconfig` files. The source layout is [the frontend standard](../platform/frontend-standard.md) Section 2. There are
no `VITE_*` or other build-time variables: every URL is relative, so one build serves every environment.

| Path under `frontend/` | Holds |
| --- | --- |
| `src/` | Application code and its colocated `*.test.ts(x)` files; the only tree measured for coverage |
| `src/api/generated/` | `schema.d.ts` and `operations.ts`, generated from `docs/api/openapi-v1.yaml`; committed, never edited |
| `test/` | Test setup, the schema-typed MSW handlers, render helpers, and the fake socket; not measured for coverage |
| `scripts/` | The TSDoc checker, API generator and drift check, bundle check, and supply-chain audit, each with tests |
| `e2e/` | Playwright specs: the browser audit (Ticket 94), and the journeys and the `testing` isolation check (Ticket 96) |

All commands run through the root `Makefile`, which calls `pnpm --dir frontend`. Only `pnpm install
--frozen-lockfile` installs; a dependency changes through `pnpm add --save-exact` or `pnpm remove`.

| Make target | Runs |
| --- | --- |
| `frontend-install` | `pnpm install --frozen-lockfile` |
| `frontend-format`, `frontend-lint`, `frontend-types` | Prettier check, ESLint with zero warnings, `tsc --noEmit` for the app and the tooling plus the OpenAPI drift check |
| `frontend-docs-standard` | The TSDoc checker |
| `frontend-test` | Vitest with coverage thresholds, parallel |
| `frontend-build` | Production build and the bundle checks |
| `frontend-check` | All of the above in order |
| `frontend-api-generate` | Regenerates `src/api/generated/` from the committed OpenAPI document |
| `frontend-e2e` | `node e2e/support/run.ts`: isolation check, build, throwaway database, cache namespaces, and Mailpit in `testing`, then Playwright (the mocked audit project and the journeys on three browsers), a log-window check, cleanup, and a residue check; see [service inventory](../platform/service-inventory.md) Section 4.3 |
| `frontend-image` | The pinned container build |
| `frontend-image-check` | Standalone image, headers, injected-script CSP block, cache, health, and candidate Traefik routing proof |
| `frontend-audit` | Offline licence, advisory, release-age, and install-script audit; requires fresh matching evidence |
| `frontend-audit-refresh` | Explicit dependency-resolution boundary; retrieves and validates full-lock registry and advisory evidence |
| `frontend-advisory-evidence` | Machine-readable pnpm advisory document for the refresh command |

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
| Dependencies | Exact pins, each at least seven days old (`minimumReleaseAge` with no exclusions), project licence `MIT`, zero install-time scripts |
| Documentation | No comments; a TSDoc block on every module and exported symbol, checked by `frontend-docs-standard` |

## 5. Browser support

| Browser | Support |
| --- | --- |
| Chromium and Firefox, current stable | Supported and tested by Playwright on the HTTPS origin and on the plain-HTTP loopback origin |
| Chromium and Firefox, previous stable | Supported; not separately tested, because Playwright runs only the build it bundles |
| WebKit, current stable | Supported and tested on HTTPS origins; unsupported on plain-HTTP local origins, where it rejects the `Secure` session cookies (observed 2026-10-11) |
| Older or other engines | Unsupported |

## 6. Delivery

- **Image.** `docker/frontend/Dockerfile`: digest-pinned Node build stage that enables the pinned pnpm through Corepack
  and runs `pnpm install --frozen-lockfile`, then digest-pinned `nginx-unprivileged`, non-root, read-only filesystem,
  no package manager, no secret, no volume.
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

## 8. Replan record

On 2026-10-10 the user directed pnpm in place of npm and asked for tickets 87–99 to be replanned before work
proceeded. The ticket order, scope, route map, and gate are unchanged; the corrections below are folded into the tickets.

| Finding | Resolution | Tickets |
| --- | --- | --- |
| The package manager was npm | pnpm 11.21.0 everywhere: ADR 0028 amended, `AGENTS.md`, the standard, prerequisites, `preflight`, Make targets, the image build, and the hooks | 87, 97, 98 |
| The first manifest declared `UNLICENSED` | `MIT`, matching `LICENSE` and the backend | 87 |
| ESLint 9 is unsupported, and TypeScript 7 breaks two peer ranges | ESLint 10.12.0 with a recorded peer override for `jsx-a11y`; TypeScript 5.9.3 | 87 |
| pnpm excluded three too-new packages from its own release-age gate | A seven-day gate with no exclusions; nine pins moved to older releases | 87, 98 |
| Several required packages were missing from the ADR | `openapi-typescript`, `yaml`, `axe-core`, the Tailwind Vite plugin, and the Playwright axe package recorded with dated evidence | 87 |
| "Types derive from the schema" had no mechanism | `scripts/generate-api.ts` writes `src/api/generated/` and `--check` fails the type check on drift | 87, 88 |
| Callers could not know a route's documented statuses at run time | The generated `operations.ts` table drives the client's unexpected-status rule and the exhaustive status tests | 88 |
| The coverage tree was ambiguous | Coverage measures `src/` only; helpers live in `test/`, tooling in `scripts/`, and the script tests run in the suite | 87, 95 |
| The TSDoc checker had no definition | Rules are fixed in the frontend standard Section 9 | 87 |
| Fragment credentials must leave the URL before anything renders | `src/session/fragment.ts` is called by the bootstrap before the first render and is fully tested | 89, 90, 91 |
| A five-minute access token needs session upkeep | The client refreshes once, single-flight, then retries once; a second `401` signs out | 88, 89 |

Sequence, one commit per ticket: 87, 88, 89, 90, then 91, 92, and 93, then 94 and 95, then 96 and 97, then 98, then 99.

### 8.1 Findings from real-browser runs

Ticket 96 drove the built application in Chromium, Firefox, and WebKit against the `testing` backend on 2026-10-11.

| Finding | Resolution | Ticket |
| --- | --- | --- |
| `PATCH` and `DELETE` on `users/me/`, `set_password/`, and `set_username/` rejected a wrong CSRF token with `403` but did not document it, so the client rightly called it unexpected | Backend contract defect: Phase 11 was reopened and the four operations now document the enveloped `403`, with the evidence tests observing it ([the Phase 11 handover](../handover/phase-11.md) Section 10) | 96 |
| A throttled repeat left the earlier "Request received" message beside the error | The two request pages clear the message when the next attempt starts, with regression tests | 90, 91 |
| A refused request showed only "The request failed" | The `forbidden` kind has its own title and a "Reload the page, then try again" hint | 89 |
| The Phase 11 manifest held only secondary evidence about prefixed cookies on plain HTTP | Observed: Chromium and Firefox keep them on loopback HTTP, WebKit does not; the journeys run on HTTPS in all three and an origin test pins the HTTP result | 96 |
| Previous-stable browsers were listed as tested | Corrected above: only the bundled current builds are tested | 96 |
