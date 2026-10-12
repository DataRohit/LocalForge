---
status: accepted
date: 2026-10-08
amended: 2026-10-11
---

# Frontend supporting toolchain

TypeScript in strict mode, Vite, React Router in library (SPA) mode, TanStack Query, React Hook Form with Zod,
Vitest with React Testing Library and MSW, Playwright with axe, ESLint, Prettier, and pnpm on Node 24 LTS. All
versions are exact, committed in `pnpm-lock.yaml`, and installed with `pnpm install --frozen-lockfile`.

Amended 2026-10-10 on the user's instruction: the package manager is **pnpm**, not npm. The same amendment records the
resolved pins, the release-age policy, and the compatibility decisions Ticket 87 made while resolving them.

## Choices and reasons

| Area | Choice | Reason |
| --- | --- | --- |
| Language | TypeScript 5.9.3, strict | Matches the repository's mypy-strict standard; see "Compatibility decisions" |
| Bundler | Vite | Static build output, Tailwind plugin, no server runtime |
| Routing | React Router, SPA mode | Client routing only; its framework mode adds a server |
| Server state | TanStack Query | One owner for fetched data, retries, and cache invalidation |
| Forms | React Hook Form and Zod | Usability validation only; backend errors stay authoritative |
| API types | `openapi-typescript` and `yaml` | Types and a status-code table generated from `docs/api/openapi-v1.yaml`; drift fails the type check |
| Unit and integration tests | Vitest, React Testing Library, MSW, axe-core | Vite-native; request mocking without a server; accessibility rules in jsdom |
| Browser tests | Playwright, `@axe-core/playwright` | Real browser against the `testing` backend; accessibility rules |
| Browser test origin | `selfsigned` | A throwaway loopback certificate, so the suite can serve the HTTPS origin the secure cookies need without committing a private key |
| Quality | ESLint 10, Prettier | Type-aware lint plus a no-comments docstring check |
| Package manager | pnpm 11.21.0 | Mandated by the user; strict `node_modules`, a lockfile that records integrity, and a native release-age gate |
| Runtime | Node 24 LTS | Matches the host; pinned by `.node-version` and image digest |

## Package manager rules

- `packageManager` in `frontend/package.json` pins the pnpm version. Corepack provides it in the container build.
- `pnpm install --frozen-lockfile` is the only install path in the gate, the image build, and CI. A dependency change
  is made with `pnpm add --save-exact` or `pnpm remove`, and the lockfile change is committed with it.
- **Release-age gate.** `pnpm-workspace.yaml` sets `minimumReleaseAge: 10080` (seven days) with
  `minimumReleaseAgeStrict: true`. No version younger than seven days may be installed, directly or transitively, and
  `minimumReleaseAgeExclude` stays empty: a package too new to pass is pinned to its previous release instead. On
  2026-10-10 pnpm flagged `prettier`, `msw`, and `@types/node`, and nine direct pins in total were under seven days
  old; each was moved to the newest same-major release older than seven days.
- **Install scripts.** pnpm blocks dependency lifecycle scripts by default. The resolved lockfile contains no
  `requiresBuild` entry. Ticket 98 also checks the registry metadata for every resolved package: the optional
  macOS-only `fsevents` 2.3.3 declared an install hook despite that missing marker. The exact `fsevents: '-'`
  override removes it rather than accepting its hook; Vite retains its portable watcher. Both recorded override
  keys and values are audited. Nothing in the resolved tree declares an install-time script.
- **Licence of the project.** `frontend/package.json` declares `MIT`, matching the repository `LICENSE` and
  `backend/pyproject.toml`. `private: true` prevents accidental publication.

## Compatibility decisions

| Decision | Evidence (2026-10-10) |
| --- | --- |
| TypeScript **5.9.3**, not 7.0.2 | `typescript-eslint` 8.71 declares `typescript >=4.8.4 <6.1.0` and `openapi-typescript` 7.13 declares `^5.x`; 7.0.2 is the registry latest but fails both ranges |
| ESLint **10.12.0**, not 9.39.5 | The registry marks 9.39.5 "no longer supported". `eslint-plugin-jsx-a11y` 6.10.2 declares only `eslint ^9`, so `pnpm-workspace.yaml` overrides its `eslint` peer to 10.12.0. A probe on 2026-10-10 ran its `strict` flat config under ESLint 10 and reported `alt-text`, `click-events-have-key-events`, and `no-noninteractive-element-interactions` as expected |
| `react-router` 8.4.0 | Requires Node 22.22 or newer and React 19.2.7 or newer; both hold |
| `vitest` 5.0.3 with `vite` 8.3.2 | Peer range `vite ^6.4 \|\| ^7 \|\| ^8` and Node `^22.12 \|\| ^24`; `@vitest/coverage-v8` is pinned to the same version |
| `@types/node` **24.19.1** | Matches the Node 24 runtime rather than the registry's Node 26 types |
| `selfsigned` 5.5.0 | Development and test only; generates a one-day loopback certificate in memory-safe JavaScript, with no install script. Its dependency tree (checked 2026-10-11 with `pnpm licenses list`) is MIT, BSD-3-Clause, Apache-2.0, and 0BSD only. Added because `detect-private-key` forbids a committed key and OpenSSL is not a listed prerequisite |
| `axe-core` **MPL-2.0**, `@axe-core/playwright` **MPL-2.0** | Development and test only, never bundled; file-level copyleft accepted under the same terms as the MPL-2.0 tools in [0015](./0015-reject-restricted-licenses.md) |

## Evidence

### Resolved-tree licence disposition, 2026-10-11

Ticket 98 retrieves version-specific metadata for every package in the lockfile, not only installed packages.
MIT, MIT-0, ISC, Apache-2.0, BSD-2-Clause, BSD-3-Clause, 0BSD, Python-2.0, BlueOak-1.0.0, and MPL-2.0 are accepted
under ADR 0015. `(MIT OR CC0-1.0)` uses its MIT alternative. SPDX's primary licence-list data confirms the
OSI status of Python-2.0, BlueOak-1.0.0, MIT-0, and 0BSD; CC-BY-4.0 and CC0-1.0 are **not** OSI-approved.

The bounded data exceptions are `caniuse-lite` under CC-BY-4.0 (browser-support data with attribution), and
`language-subtag-registry` and `mdn-data` under CC0-1.0 (public-domain language and CSS metadata). These build-tool
datasets are accepted as data, not a licence exception for arbitrary software. The audit permits those exact
package/licence pairs only. Unknown licences and all other CC-BY/CC0 packages fail closed. No install-hook,
release-age, or advisory exception is accepted.

Primary evidence: version metadata at `https://registry.npmjs.org/<package>/<version>`, the caniuse-lite licence
at `https://github.com/browserslist/caniuse-lite/blob/main/LICENSE`, and SPDX's maintained licence data at
`https://github.com/spdx/license-list-data/blob/main/json/licenses.json`. pnpm documents dependency removal
overrides at `https://pnpm.io/settings#overrides`. Full build and browser gates verify portable watcher behavior.

`make frontend-audit-refresh` is the explicit dependency-resolution network boundary. It saves a fingerprinted
snapshot only after a complete passing scan. Ordinary `make frontend-audit` is offline and rejects absent,
malformed, older-than-24-hour, future-dated, or input-mismatched evidence. Refresh when dependencies change or
before final verification; cache files are ignored under `frontend/.e2e-run/` and are never application assets.
Starting a refresh invalidates the previous snapshot, so a failed or interrupted fresh scan cannot fall back to
an earlier successful verdict for the same pins.

Resolved on 2026-10-10 from `registry.npmjs.org` with `npm view <package> time --json` and
`npm view <package>@<version> license`. Age is days from publication to 2026-10-10. Every direct pin is at least seven
days old. [0016](./0016-accept-release-lag.md) and the CI-matrix standard apply to any package added later.

| Package | Version | Group | Published | Age | Licence |
| --- | --- | --- | --- | ---: | --- |
| `@hookform/resolvers` | 5.9.1 | runtime | 2026-08-17 | 54 | MIT |
| `@tanstack/react-query` | 5.104.1 | runtime | 2026-10-02 | 8 | MIT |
| `react` | 19.3.0 | runtime | 2026-09-09 | 31 | MIT |
| `react-dom` | 19.3.0 | runtime | 2026-09-09 | 31 | MIT |
| `react-hook-form` | 7.89.0 | runtime | 2026-09-26 | 15 | MIT |
| `react-router` | 8.4.0 | runtime | 2026-09-15 | 25 | MIT |
| `zod` | 4.6.5 | runtime | 2026-09-13 | 27 | MIT |
| `@axe-core/playwright` | 4.13.0 | development | 2026-08-11 | 60 | MPL-2.0 |
| `@eslint/js` | 10.0.1 | development | 2026-02-06 | 246 | MIT |
| `@playwright/test` | 1.63.0 | development | 2026-09-04 | 36 | Apache-2.0 |
| `@tailwindcss/vite` | 4.3.3 | development | 2026-07-16 | 86 | MIT |
| `@testing-library/dom` | 10.4.2 | development | 2026-09-13 | 27 | MIT |
| `@testing-library/jest-dom` | 7.0.1 | development | 2026-08-09 | 62 | MIT |
| `@testing-library/react` | 16.3.3 | development | 2026-08-27 | 44 | MIT |
| `@testing-library/user-event` | 14.6.7 | development | 2026-09-02 | 39 | MIT |
| `@types/node` | 24.19.1 | development | 2026-10-01 | 9 | MIT |
| `@types/react` | 19.3.0 | development | 2026-09-09 | 31 | MIT |
| `@types/react-dom` | 19.3.0 | development | 2026-09-09 | 31 | MIT |
| `@vitejs/plugin-react` | 6.1.1 | development | 2026-08-28 | 44 | MIT |
| `@vitest/coverage-v8` | 5.0.3 | development | 2026-09-30 | 10 | MIT |
| `axe-core` | 4.13.0 | development | 2026-08-05 | 66 | MPL-2.0 |
| `eslint` | 10.12.0 | development | 2026-10-02 | 8 | MIT |
| `eslint-plugin-jsx-a11y` | 6.10.2 | development | 2024-10-26 | 715 | MIT |
| `eslint-plugin-react-hooks` | 7.1.1 | development | 2026-04-17 | 176 | MIT |
| `globals` | 17.13.0 | development | 2026-10-01 | 10 | MIT |
| `jsdom` | 30.1.1 | development | 2026-09-22 | 19 | MIT |
| `msw` | 3.0.2 | development | 2026-10-03 | 8 | MIT |
| `openapi-typescript` | 7.13.0 | development | 2026-02-11 | 241 | MIT |
| `prettier` | 3.9.9 | development | 2026-09-23 | 17 | MIT |
| `prettier-plugin-tailwindcss` | 0.8.1 | development | 2026-07-15 | 87 | MIT |
| `tailwindcss` | 4.3.3 | development | 2026-07-16 | 86 | MIT |
| `typescript` | 5.9.3 | development | 2025-09-30 | 375 | Apache-2.0 |
| `typescript-eslint` | 8.71.0 | development | 2026-09-28 | 12 | MIT |
| `vite` | 8.3.2 | development | 2026-10-01 | 9 | MIT |
| `vitest` | 5.0.3 | development | 2026-09-30 | 10 | MIT |
| `selfsigned` | 5.5.0 | development | 2026-01-12 | 272 | MIT |
| `yaml` | 2.9.1 | development | 2026-09-11 | 29 | ISC |

Release lines recorded before resolution (2026-10-08 web snapshots) were React 19.2.x, Tailwind CSS 4.3.x, Vite 8.1,
React Router 8.3.x, TanStack Query 5.x, Vitest 4.1.x, and Playwright 1.60 to 1.61. The registry showed newer lines on
2026-10-10, which are the ones pinned above.

## Constraints

- No dependency with a restricted licence, telemetry, or runtime network access
  ([0015](./0015-reject-restricted-licenses.md)).
- Only dependency resolution and the one-time Playwright browser download touch the network.
- A new package needs an ADR update with dated evidence.

## Considered options

Webpack, Jest, Cypress, SWR, Next.js, and Remix were not chosen: heavier configuration, a server runtime, or no
advantage over the selected tools. npm was the original package manager choice and was replaced by pnpm at the user's
direction; Yarn and Bun were not considered because the user named pnpm.
