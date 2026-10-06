# Phase 10 backend-first monorepo handover

## Current acceptance status: GO

Revalidated on **2026-10-06** from a clean Docker state. The complete supported gate passes in both host and
container modes, and the integration audit passes. Immutable image references now govern convention, image,
history, and runtime security checks during fresh setup.

Handover completed **2026-10-05** after Tickets 71–78. The repository now has one global root and an explicit
`backend/` Python project. The migration changed repository ownership and command paths while preserving the
application surface, the two Compose environments, registered service names, ports, secrets, and dependency pins.
No frontend implementation or third environment was added.

## Final ownership

| Ownership | Paths and contract |
| --- | --- |
| Repository global | `.env.example`, `.env.*.sops`, `.sops.yaml`, Compose files, `docker/`, `docs/`, `.scratch/`, `.github/`, policy files, `AGENTS.md`, `CONTEXT.md`, `README.md`, and `Makefile` |
| Backend project | `backend/pyproject.toml`, `backend/uv.lock`, `backend/.python-version`, `backend/.venv/`, `backend/src/`, `backend/tests/`, and `backend/scripts/` |
| Future frontend reservation | `frontend/` is reserved by documentation only; no directory, package manifest, lockfile, source, or toolchain exists |

Compose remains global because it owns the complete deployment boundary. Docker build contexts and copy paths point at
`backend/`, while normalized container paths, service names, networks, volumes, ports, health checks, and environment
names remain unchanged.

## Command compatibility

The root `Makefile` is the sole supported operator entry point. It resolves the repository root from the Makefile
location, selects `backend/` as the uv project, and forwards every declared Poe task. It works from the repository
root and from another directory with `make -f <repository-root>/Makefile <task>`.

| Previous path or assumption | Phase 10 decision |
| --- | --- |
| `localforge.ps1` and `localforge.sh` wrappers | Deleted. There is no compatibility alias; use `make <task>`. |
| Root `uv run poe <task>` | Replaced by `make <task>`, with backend project selection handled by the Makefile. |
| Root Python metadata and source paths | Moved under `backend/`; root commands retain explicit backend resolution. |
| Direct backend maintenance commands | Use the corresponding Make target and pass task options through `ARGS="..."`. |
| Historical Phase 1–9 transcripts | Preserved as historical evidence with translation notes where their original pre-move commands remain relevant. |
| Compose and environment paths | Kept global; both `localforge-dev` and `localforge-test` remain the only environments. |

The Poe task names remain unchanged. Root policy, CI, hooks, documentation, Docker contexts, imports, coverage paths,
and generated OpenAPI evidence all point to the new ownership boundary.

## Verification evidence

The following evidence closed Ticket 77 and was refreshed for this handover:

- `make sync`, `make preflight`, `make setup`, `make pre-commit`, and the full `make check` gate passed. The full gate
  passed Django checks, OpenAPI checks, Ruff, structured documentation, mypy, ty, architecture tests, 2,159 parallel
  core tests at 100% branch coverage, 23 security timing tests, host integration, and registration timing stability.
- The later GNU Make prerequisite addition was covered by `make preflight` (all 14 checks, including every required
  check), `make test-unit`
  (1,536 selected tests passed; 646 deselected), and `make pre-commit` (all hooks passed).
- `make secrets-decrypt` recovered both committed encrypted environments. Generated development, testing, and
  testing-host files matched the documented key contract, contained no placeholders, and were not committed.
- `make environments-setup` rebuilt the local images, recreated both Compose projects, waited for required health
  checks, and passed the project-scoped Docker inventory. `make development-health` and `make testing-health` passed.
- The deployed public seam `http://127.0.0.1:8080/health/` with host `localforge.localhost` returned HTTP 200 and
  readiness `ready`; database primary and replica, cache, channel layer, broker, object storage, and mail checks were
  working.
- `make docker-audit`, `make convention-audit`, `make security-audit`, and `make security-audit-runtime` passed.
  Image policy digests, dependency checks, deployment checks, exposure checks, history checks, and runtime-secret
  checks matched the repository policy.
- Fresh bounded logs for every container in `localforge-dev` and `localforge-test` contained no unexplained warning,
  error, critical, restart, or unhealthy finding after the environments stabilized.
- Repository scans found no stale operational wrapper reference, duplicate backend project, untracked secret, or frontend
  implementation. `make testing-integration-audit` passed its mail persistence, SMTP, cache degradation, and cleanup
  checks. `git diff --check` and the final pre-commit hooks passed.

### Runtime command record

- Health commands: `make development-health` and `make testing-health`.
- Behavior command: `Invoke-WebRequest -Uri http://127.0.0.1:8080/health/ -Headers @{ Host =
  'localforge.localhost'; Accept = 'application/json' }`, which returned HTTP 200 with readiness `ready`.
- Development log command: `docker compose --project-name localforge-dev logs --since
  2026-10-05T13:28:18.104657Z --until 2026-10-05T13:49:11.215557Z --no-color`. Testing log command: `make check`,
  whose testing audit executes `docker logs --since 2026-10-05T13:28:18.104657Z --until
  2026-10-05T13:49:11.215557Z <container>` for each `localforge-test` container. Collection, test, post-check, and
  log findings were all zero. The development rebuild used the same health, ownership, and bounded-log disposition
  recorded in Ticket 77.
- Disposition: no unexplained warning, error, critical, restart, unhealthy, missing-service, or failure-level success
  response remained after stabilization; the startup readiness race was excluded from the accepted window.

The first readiness probe immediately after recreation can observe the documented startup race. The accepted runtime
window is the post-stabilization window captured after health checks passed; that window was clean.

## Rollback and recovery

Rollback is a Git and environment operation. It does not require regenerating secrets or deleting Docker data.

1. Record the current commit and stop both environments with `make development-down` and `make testing-down`.
2. Preserve `.env.development.sops`, `.env.testing.sops`, `.sops.yaml`, and any named volumes. Never run
   `docker system prune -a` on this shared machine.
3. Revert the Ticket 78 handover commit first, then revert the remaining Phase 10 commits in reverse order:
   `7b0474d`, `707cedb`, `f57608e`, `cc34e7e`, `b1001b2`, `6774d30`, `d2fdfa2`, and `a0768b6`.
4. Recreate the selected revision's documented environment and run its supported setup and health gates before
   accepting traffic. The encrypted files remain outside Docker and are not changed by the rollback.
5. If only the Makefile command surface is being investigated, restore the prior revision and use the commands
   documented by that revision; do not recreate deleted wrappers manually.

Rollback is complete only after both Compose projects are healthy, `/health/` is ready, bounded logs are clean, and the
security and Docker audits pass for the selected revision.

## Frontend handoff and stop condition

After Phase 10 acceptance, frontend work requires a new ticket defining its toolchain, lockfile, build
context, environment contract, Make targets, CI and pre-commit ownership, and any shared documentation or policy
changes. The future frontend must not create a third environment, move global secrets, or change the fixed backend
route and WebSocket surface without a separate governing decision.

The Phase 10 gate passes: all tickets 71–78 are done, ownership matches the architecture and ADR, the root Makefile
works independently of caller directory, and both environments remain healthy. Encrypted environments decrypt,
backend quality and runtime truth gates pass, no stale root backend path remains, and no frontend files were added.
