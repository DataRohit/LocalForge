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
