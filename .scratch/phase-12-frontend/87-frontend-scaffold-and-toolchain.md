# 87: Frontend scaffold and toolchain

**What to build:** The `frontend/` project with React, Tailwind CSS, strict TypeScript, exact-pinned dependencies
installed by pnpm, a committed lockfile, and the quality tooling, wired to root Make targets.

**Blocked by:**

- [86: Backend freeze manifest](../phase-11-backend-freeze/86-backend-freeze-manifest.md)

**Governing sources:**

- [Frontend toolchain ADR](../../docs/adr/0028-frontend-toolchain.md)
- [React and Tailwind ADR](../../docs/adr/0026-react-and-tailwind-frontend.md)
- [Frontend standard](../../docs/platform/frontend-standard.md)
- [Phase 12 architecture](../../docs/architecture/phase-12-frontend.md)
- [Phase 12 replan record](../../docs/architecture/phase-12-frontend.md)
- [Ticket index](../README.md)

**Status:** done (Locally Verified)

- [x] Every dependency is an exact version resolved from registry metadata on 2026-10-10 and at least seven days old,
      with versions, dates, and licences recorded in the toolchain ADR; `pnpm-lock.yaml` is committed,
      `pnpm install --frozen-lockfile` is the only install path, and `minimumReleaseAgeExclude` is empty.
- [x] `package.json` declares licence `MIT` and `private`, pins pnpm in `packageManager`, and `.node-version` pins Node;
      the lockfile has no entry that requires a build script, and no dependency has a restricted licence or telemetry.
- [x] Tailwind is compiled into local bundled styles, and the build fails on any remote URL in the output.
- [x] The Make targets in the Phase 12 architecture exist (`frontend-install`, `-format`, `-lint`, `-types`,
      `-docs-standard`, `-test`, `-build`, `-check`, `-api-generate`, `-e2e`, `-image`, `-audit`), `frontend-check` is green,
      and `make preflight` checks pnpm instead of npm.
- [x] `scripts/generate-api.ts` writes `src/api/generated/schema.d.ts` and `operations.ts` from the committed OpenAPI
      document, and `--check` (part of `frontend-types`) fails when the committed files differ, proven by a test.
- [x] The build enforces the bundle budgets (200 kB gzip JavaScript, 25 kB gzip CSS) and has no `VITE_*` or other
      build-time variables.
- [x] Coverage thresholds are 100% for statements, branches, functions, and lines over `src/`, with only the two
      documented exclusions; tests have per-test timeouts, run in parallel, and fail on `console.error` or `console.warn`.
- [x] The TSDoc checker implements the frontend standard Section 9 and has its own tests; an import-direction test and
      a single-network-module test run in the suite.
- [x] Pre-commit hooks run Prettier, ESLint, and the TSDoc checker on frontend files.
- [x] `.gitignore`, `.dockerignore`, pre-commit hooks, and `.markdownlint` coverage are updated together.
- [x] The files created are listed in the build plan; no Compose or runtime change is made.
- [x] Status recorded as Locally Verified.

**Verification (2026-10-10, no runtime change):**

- `make frontend-install` (`pnpm install --frozen-lockfile`) and `make frontend-check` pass: Prettier clean, ESLint with
  zero warnings, `tsc -b` for the app and tooling plus the OpenAPI drift check, the documentation checker with no
  violations, 7 test files and 121 tests under the parallel runner with the coverage thresholds at 100%, and the build
  with the bundle checks (67,788 of 200,000 gzip bytes of JavaScript, 1,855 of 25,000 of CSS, no remote URL beyond
  the three inert identifier families on the allowlist, no inline script).
- `pnpm run audit:supply` passes: licence `MIT`, private, `packageManager` pinned, every pin exact, the seven-day
  strict release-age gate with no exclusions, no `requiresBuild` entry in the lockfile, registry-only resolutions.
- `make preflight` row 16 reports pnpm 11.21.0; the backend preflight, compose-foundation, and environment-isolation
  tests pass (291 tests).
- The generated API files are committed; `--check` fails on drift, proven by `scripts/generate-api.test.ts`.
- Nothing was started, rebuilt, or applied. `development` was not read or written; `testing` needed no run because the
  ticket adds no runtime, Compose, or service change.

**Findings and dispositions:**

- npm was the planned package manager; replaced by pnpm at the user's direction and recorded in the toolchain ADR.
- The first manifest said `UNLICENSED`; corrected to `MIT`.
- ESLint 9.39.5 is flagged unsupported and TypeScript 7.0.2 breaks two peer ranges; ESLint 10.12.0 with a recorded
  `jsx-a11y` peer override and TypeScript 5.9.3 are pinned, each with its evidence.
- pnpm added three entries to its own release-age exclusions; removed, and nine direct pins moved to releases at
  least seven days old. The audit now fails on any exclusion.
- The documentation checker's tests found two real defects (same-line trailing comments and comments inside JSX
  braces); both fixed before the checker was trusted.
- The checker rule for types and constants was relaxed to one description line; the standard was updated to match.
- Coverage is trivially 1 of 1 statements today; it becomes meaningful from Ticket 88.

Disposition: complete.
