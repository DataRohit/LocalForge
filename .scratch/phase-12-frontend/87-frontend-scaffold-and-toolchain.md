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

**Status:** pending

- [ ] Every dependency is an exact version resolved from registry metadata on 2026-10-10 and at least seven days old,
      with versions, dates, and licences recorded in the toolchain ADR; `pnpm-lock.yaml` is committed,
      `pnpm install --frozen-lockfile` is the only install path, and `minimumReleaseAgeExclude` is empty.
- [ ] `package.json` declares licence `MIT` and `private`, pins pnpm in `packageManager`, and `.node-version` pins Node;
      the lockfile has no entry that requires a build script, and no dependency has a restricted licence or telemetry.
- [ ] Tailwind is compiled into local bundled styles, and the build fails on any remote URL in the output.
- [ ] The Make targets in the Phase 12 architecture exist (`frontend-install`, `-format`, `-lint`, `-types`,
      `-docs-standard`, `-test`, `-build`, `-check`, `-api-generate`, `-e2e`, `-image`, `-audit`), `frontend-check` is green,
      and `make preflight` checks pnpm instead of npm.
- [ ] `scripts/generate-api.mjs` writes `src/api/generated/schema.d.ts` and `operations.ts` from the committed OpenAPI
      document, and `--check` (part of `frontend-types`) fails when the committed files differ, proven by a test.
- [ ] The build enforces the bundle budgets (200 kB gzip JavaScript, 25 kB gzip CSS) and has no `VITE_*` or other
      build-time variables.
- [ ] Coverage thresholds are 100% for statements, branches, functions, and lines over `src/`, with only the two
      documented exclusions; tests have per-test timeouts, run in parallel, and fail on `console.error` or `console.warn`.
- [ ] The TSDoc checker implements the frontend standard Section 9 and has its own tests; an import-direction test and
      a single-network-module test run in the suite.
- [ ] Pre-commit hooks run Prettier, ESLint, and the TSDoc checker on frontend files.
- [ ] `.gitignore`, `.dockerignore`, pre-commit hooks, and `.markdownlint` coverage are updated together.
- [ ] The files created are listed in the build plan; no Compose or runtime change is made.
- [ ] Status recorded as Locally Verified.
