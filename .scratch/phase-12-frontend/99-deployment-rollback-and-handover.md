# 99: Deployment, rollback, and handover

**What to build:** The exact procedures to deploy the frontend into `development` and roll it back, plus the Phase 12
handover for Gate 4.

**Blocked by:**

- [98: Frontend security and supply-chain audit](98-frontend-security-and-supply-chain-audit.md)

**Governing sources:**

- [Frontend hosting and edge routing ADR](../../docs/adr/0027-frontend-hosting-and-edge-routing.md)
- [Full-stack pivot ADR](../../docs/adr/0024-full-stack-pivot.md)
- [Build plan](../../docs/build/plan.md)
- [Phase 12 replan record](../../docs/architecture/phase-12-frontend.md)
- [Ticket index](../README.md)

**Status:** Locally Verified (2026-10-12); deployment not applied and awaiting the user's authorization

## Verification record, 2026-10-12

- **Runbooks.** `docs/runbooks/frontend-deployment/deploy.md` lists every command in order: preconditions,
  baseline, apply, health, behavior through the deployed seam, bounded log window, and permanent adoption. Each step
  that reads or changes `development` is marked `[AUTHORIZATION REQUIRED]`. `rollback.md` stops the frontend,
  recreates Django from the unmodified files, removes the frontend, reverts the registry patch, and verifies.
  One finding is recorded in both: `make development-up` and `-rebuild` pass only the two base Compose files, so
  the candidate must be merged into `compose.development.yaml` or a routine rebuild silently rolls routing back.
- **Rollback rehearsal.** `frontend/scripts/check-image.ts` now stops the disposable Traefik after the candidate
  routing passes and restarts it with the pre-candidate rule read from the committed `compose.development.yaml`
  label. It requires: the application shell gone from `/account`, the backend seam (`/api/v1/browser/csrf/`) still
  `204`, the frontend container stateless (no mounts), and clean bounded logs in both routing states. Two red-green
  unit groups cover `routingDocument` and `baselineRule`. `make frontend-image-check` passed over
  **2026-10-12T00:42:42Z-00:43:06Z** against the `testing` backend (CSP block observed, candidate routing, rollback,
  clean logs, cleanup, no residue). No `development` object was read or changed.
- **Gates.** `make frontend-check` status 0 over 00:45:31Z-00:46:30Z: **1,130** tests in 50 files, 100% coverage over
  `src/`, bundle unchanged at 136,755/200,000 gzip bytes. `make convention-audit-testing` passed at 00:46:37Z with no
  residual `frontend-fe6vn` or Mailpit container. The host and container backend gates were not rerun: no backend
  file changed after Ticket 98's passing records.
- **Handover and Gate 4.** `docs/handover/phase-12.md` records ownership, commands, evidence, and the unverified
  production gates, and carries the Gate 4 report.
- **Review.** Inline diff review against the criteria found one defect: the docs checker rejected an over-long
  docstring line, fixed and the gate rerun. Nothing was applied to `localforge-dev`.

- [x] A runbook lists every command to deploy the candidate files, the health and behavior checks to run, and the
      bounded log window to inspect, each marked as requiring the user's authorization.
- [x] A rollback procedure restores the previous routing with no data loss and is rehearsed against `testing` or a
      standalone project.
- [x] The handover records final ownership, commands, evidence, and the unverified production gates.
- [x] Nothing has been applied to `localforge-dev`.
- [x] Gate 4 report is delivered; deployment waits for the user's explicit authorization.

## Adoption record, 2026-10-12

The user asked for the frontend to be deployed in `development` and for the project to be completed in one run. The
inert candidate was adopted into the committed development definition instead of being applied by hand, so
`make environments-setup` and every `make development-*` command deploy it:

- `compose.development.yaml` gained `frontend-fe6vn` and the Django owned-prefix router labels at priority 100.
- `backend/scripts/manage_platform.py` registers the container, its image, its build target, and its rebuild service;
  `backend/scripts/audit_naming.py` registers its network membership and absence of published ports.
- `frontend/scripts/check-image.ts` reads the committed labels, and the rollback baseline is the frontend's own
  whole-host rule. `compose.candidate.yaml` and `registry.candidate.patch` were retired; the runbooks, inventory,
  conventions, Kubernetes mapping, ADR 0027, plan, and handover were updated.
- Verified without touching any Docker object: a merged `docker compose config` of both development files is valid
  and lists 24 services; the unit tests for Compose, platform, and naming pass (the only failures are the existing
  ones that need generated environment files); Ruff, Ruff format, the documentation standard, markdownlint, yamllint,
  and `make frontend-check` (1,133 tests at 100% coverage) pass.
- Full backend run on 2026-10-12 (Block 2, 34m55s, all six steps passed): `make environments-setup` built and started
  `frontend-fe6vn` healthy among 24 development containers with the project-scoped Docker audit passing; `make check`
  passed with 2,303 tests at 100% branch coverage, 23 security-timing tests, and five registration-stability runs;
  `make testing-test-container` passed 2,303 and 23 tests with a clean `testing` log window; and
  `make testing-integration-audit` passed. The first `make check` failed once on a minute-boundary race in a
  per-minute throttle test; the integration harness now waits out the end of a window, with no limit weakened.
- Still unrecorded: the runtime-truth checks in `deploy.md` Sections 3 to 5 (frontend behavior through Traefik and
  the public edge, and the `development` log window). The status stays **Locally Verified** until that evidence is
  recorded.
