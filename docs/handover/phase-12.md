# Phase 12 frontend handover

Status: **Locally Verified; Gate 4 report delivered; frontend adopted into the development definition on the user's
instruction of 2026-10-12.** Tickets 87–99 are complete. Verification before the adoption addressed `testing` and
standalone containers only; the adoption itself is exercised by `make environments-setup` and the deploy runbook's
health, behavior, and log checks. Production runtime truth is an unverified gate.

Authoritative scope: [Phase 12 architecture](../architecture/phase-12-frontend.md). Decisions:
[ADR 0026](../adr/0026-react-and-tailwind-frontend.md), [ADR 0027](../adr/0027-frontend-hosting-and-edge-routing.md),
[ADR 0028](../adr/0028-frontend-toolchain.md). Rules: [frontend standard](../platform/frontend-standard.md).
Procedures: [deploy](../runbooks/frontend-deployment/deploy.md) and
[rollback](../runbooks/frontend-deployment/rollback.md).

## 1. Gate 4 report

| Gate item | State | Evidence |
| --- | --- | --- |
| 1. Every ticket 87–99 has a complete verification record | Passed | `.scratch/phase-12-frontend/`, one record each |
| 2. `make frontend-check`, `make frontend-e2e`, `make frontend-audit` pass; backend `testing` gate passes in container and host | Passed | Section 3 |
| 3. Container runs standalone, healthy, with a clean bounded log window | Passed | Section 3, Ticket 97 and 98 records |
| 4. Committed routing, deployment procedure, and rehearsed rollback are in `docs/runbooks/frontend-deployment/` | Passed | Section 4 |
| 5. This handover records ownership, commands, evidence, and unverified production gates | Passed | This file |
| 6. Deployment into `localforge-dev` authorized by the user and verified by the deploy runbook | Adopted | Sections 4 to 6 |

The frontend is part of the committed development definition, so every `make environments-setup` and
`make development-*` start deploys it.

## 2. Final ownership

| Ownership | Paths and contract |
| --- | --- |
| Frontend project | `frontend/`: `package.json`, `pnpm-lock.yaml`, `pnpm-workspace.yaml`, `src/`, `test/`, `e2e/`, `scripts/` |
| Frontend image | `docker/frontend/Dockerfile`, image `localforge/frontend:0.1.0`, container `frontend-fe6vn`, edge network only |
| Development definition | `compose.development.yaml` service `frontend-fe6vn` and Django owned-prefix labels; registries in `backend/scripts/manage_platform.py` and `backend/scripts/audit_naming.py`; procedures in `docs/runbooks/frontend-deployment/` |
| Backend | Unchanged by this phase's closing work; the freeze in [the Phase 11 handover](./phase-11.md) stands |
| Repository global | Root `Makefile` (`frontend-*` targets), Compose files, `docs/`, `.scratch/`, `AGENTS.md` |

The stack is React 19.3.0, React Router 8.4.0, TanStack Query 5.104.1, React Hook Form 7.89.0, Zod 4.6.5, Tailwind CSS
4.3.3, Vite 8.3.2, TypeScript 5.9.3, Vitest 5.0.3, and Playwright 1.63.0, on pinned Node 24.20.0 and pnpm 11.21.0.
Every dependency is an exact pin with a committed lockfile, a seven-day release-age gate with no exclusions, and no
install-time script (the optional `fsevents` watcher is removed by an audited override).

## 3. Commands and evidence

Run sequentially; the host security-timing stage is load-sensitive and must never overlap another heavy suite.

| Command | Result |
| --- | --- |
| `make frontend-check` | 1,133 tests in 50 files; 100% statements, branches, functions, lines over `src/`; JS 136,755/200,000 gzip bytes, CSS 3,302/25,000 |
| `make frontend-e2e` | 108 browser cases (Chromium, Firefox, WebKit) against the `testing` backend; no unexpected, skipped, or flaky case |
| `make frontend-audit` | Passes offline against saved evidence for all 509 lockfile entries; zero advisories; no licence, hook, or release-age exception beyond three named data packages in ADR 0028 |
| `make frontend-image`, `make frontend-image-check` | Read-only non-root image healthy; strict headers and caching; CSP blocks an injected inline script in Chromium; committed routing and **rollback** rehearsed through standalone Traefik; clean logs |
| `make check` (host) | 2,301 core tests at 100% branch coverage plus 23 security-timing tests; passed 2026-10-12T00:11Z–00:36Z with clean health, ownership, residue, and log audits |
| `make testing-test-container` | 2,301 core and 23 timing tests, exact collection parity, clean audits (2026-10-11) |

An earlier host run failed one timing comparison while a frontend suite overlapped it; the threshold and sample count
were not changed, and a sequential rerun passed (Ticket 98 record).

## 4. Deployment and rollback

The routing, service, image build target, and registries are committed in the development definition, so routine
tooling preserves them. Rollback reverts the adopting commit, restores whole-host Django routing, loses no data
because the frontend has no volume, secret, or mount, and was rehearsed against the `testing` backend by
`make frontend-image-check`. Changes to an already running development environment are marked in the runbooks.

## 5. Unverified gates

These need evidence from the running stack and the public edge, and are not claimed until recorded:

- The health, behavior, and log evidence in the deploy runbook, taken from the running `development` stack.
- Behavior through the public Cloudflare edge at `localforge.datarohit.com`, including the blocked `/admin/` path.
- Frontend log collection by Alloy into Loki, which relies on the existing project-label discovery.

Reported status never exceeds **Locally Verified**.

## 6. Authorization needed to proceed

The user authorized the deployment on 2026-10-12. Backend code changes after the freeze reopen Phase 11; the only
backend change in the adoption is the operator and naming registry entries in `backend/scripts/`.
