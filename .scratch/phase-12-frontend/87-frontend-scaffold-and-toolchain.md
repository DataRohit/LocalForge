# 87: Frontend scaffold and toolchain

**What to build:** The `frontend/` project with React, Tailwind CSS, strict TypeScript, exact-pinned dependencies,
a committed lockfile, and the quality tooling, wired to root Make targets.

**Blocked by:**

- [86: Backend freeze manifest](../phase-11-backend-freeze/86-backend-freeze-manifest.md)

**Governing sources:**

- [Frontend toolchain ADR](../../docs/adr/0028-frontend-toolchain.md)
- [React and Tailwind ADR](../../docs/adr/0026-react-and-tailwind-frontend.md)
- [Frontend standard](../../docs/platform/frontend-standard.md)
- [Phase 12 architecture](../../docs/architecture/phase-12-frontend.md)
- [Ticket index](../README.md)

**Status:** pending

- [ ] Every dependency is an exact version resolved from registry metadata on the ticket date, with the evidence
      recorded in the toolchain ADR; the lockfile is committed and `npm ci` is the only install path.
- [ ] Node is pinned by `.node-version`; no dependency has a restricted licence, telemetry, or install-time network
      access beyond dependency resolution.
- [ ] Tailwind is compiled into local bundled styles; a test fails the build on any remote URL in the output.
- [ ] The Make targets in the Phase 12 architecture exist (`frontend-install`, `-format`, `-lint`, `-types`,
      `-docs-standard`, `-test`, `-build`, `-check`, `-e2e`, `-image`, `-audit`) and `frontend-check` is green.
- [ ] The build enforces the bundle budgets (200 kB gzip JavaScript, 25 kB gzip CSS) and has no `VITE_*` or other
      build-time variables.
- [ ] Coverage thresholds are 100% for statements, branches, functions, and lines, with only the two documented
      exclusions.
- [ ] Pre-commit hooks run Prettier, ESLint, and the TSDoc checker on frontend files.
- [ ] `.gitignore`, `.dockerignore`, pre-commit hooks, and `.markdownlint` coverage are updated together.
- [ ] The files created are listed in the build plan; no Compose or runtime change is made.
- [ ] Status recorded as Locally Verified.
