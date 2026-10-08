---
status: accepted
date: 2026-10-08
---

# Frontend supporting toolchain

TypeScript in strict mode, Vite, React Router in library (SPA) mode, TanStack Query, React Hook Form with Zod,
Vitest with React Testing Library and MSW, Playwright with axe, ESLint, Prettier, and npm on Node 24 LTS. All
versions are exact, committed in `package-lock.json`, and installed with `npm ci`.

## Choices and reasons

| Area | Choice | Reason |
| --- | --- | --- |
| Language | TypeScript, strict | Matches the repository's mypy-strict standard |
| Bundler | Vite | Static build output, Tailwind plugin, no server runtime |
| Routing | React Router, SPA mode | Client routing only; its framework mode adds a server |
| Server state | TanStack Query | One owner for fetched data, retries, and cache invalidation |
| Forms | React Hook Form and Zod | Usability validation only; backend errors stay authoritative |
| Unit and integration tests | Vitest, React Testing Library, MSW | Vite-native; request mocking without a server |
| Browser tests | Playwright, axe | Real browser against the `testing` backend; accessibility rules |
| Quality | ESLint, Prettier | Type-aware lint plus a no-comments docstring check |
| Package manager | npm | Already on the host; no extra tool |
| Runtime | Node 24 LTS | Matches the host; pinned by `.node-version` and image digest |

## Evidence

On 2026-10-08, web search snapshots showed these release lines: React 19.2.x, Tailwind CSS 4.3.x, Vite 8.1,
React Router 8.3.x, TanStack Query 5.x, Vitest 4.1.x, Playwright 1.60 to 1.61. The snapshots could not confirm the
latest patch of any package. Ticket 87 therefore resolves each exact pin from the package registry's metadata and
records the command output and date here, applying [0016](./0016-accept-release-lag.md) and the CI-matrix standard
before accepting a package.

## Constraints

- No dependency with a restricted licence, telemetry, or runtime network access
  ([0015](./0015-reject-restricted-licenses.md)).
- Only dependency resolution and the one-time Playwright browser download touch the network.
- A new package needs an ADR update with dated evidence.

## Considered options

Webpack, Jest, Cypress, SWR, Next.js, and Remix were not chosen: heavier configuration, a server runtime, or no
advantage over the selected tools.
