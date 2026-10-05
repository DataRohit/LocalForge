# Phase 10 monorepo inventory

This inventory records the repository boundary before any Phase 10 file move. It is the authoritative input for
Tickets 72–78. Snapshot date: 2026-10-04. Unqualified `src/`, `tests/`, and `scripts/` names in the snapshot are
historical; current ownership is the `Phase 10 target` column and current commands use root wrappers or `backend/` explicitly.

## Repository state

The repository contains one Django backend and no frontend implementation. There is no `frontend/` directory, frontend
package manifest, frontend lockfile, or frontend build configuration. Tickets 73 and 74 now place backend ownership
under `backend/`; root environment, Compose, docs, and policy remain global.

## Ownership map

### Root-global files

These files remain at repository root because they describe the whole repository, deployment boundary, governance,
security policy, or environment contract:

| Class | Files |
| --- | --- |
| Environment and secrets | `.env.example`, `.env.development.sops`, `.env.testing.sops`, `.sops.yaml` |
| Compose and image ignore rules | `compose.yaml`, `compose.development.yaml`, `compose.testing.yaml`, `compose.proxy-only.yaml`, `.dockerignore` |
| Repository policy | `.editorconfig`, `.gitattributes`, `.gitignore`, `.gitleaks.toml`, `.gitmessage`, `.markdownlint-cli2.jsonc`, `.markdownlint.json`, `.pre-commit-config.yaml`, `.yamllint.yml` |
| Root command entry points | `localforge.ps1`, `localforge.sh` |
| Agent and project governance | `AGENTS.md`, `CONTEXT.md`, `CONTRIBUTING.md`, `GOVERNANCE.md`, `CODE_OF_CONDUCT.md`, `COMMIT_CONVENTION.md`, `SECURITY.md`, `SUPPORT.md`, `CHANGELOG.md`, `LICENSE`, `README.md`, `skills-lock.json` |
| CI coordination | `.github/` |

The `docker/`, `docs/`, and `.scratch/` directories are also root-global. The `.agents/` directory is installed
tooling and remains read-only; it is not a project move target. `.git/` remains Git metadata.

### Backend-owned move set

Ticket 73 moves Python project metadata and its lockfile. Ticket 74 moves backend implementation and test assets:

| Class | Current root path | Phase 10 target |
| --- | --- | --- |
| Python project metadata | `pyproject.toml`, `uv.lock`, `.python-version` | `backend/pyproject.toml`, `backend/uv.lock`, `backend/.python-version` |
| Local Python environment | `.venv/` when present | `backend/.venv/` |
| Application source | `src/` | `backend/src/` |
| Backend scripts | `scripts/` | `backend/scripts/` |
| Backend tests | `tests/` | `backend/tests/` |

Generated caches such as `.mypy_cache/`, `.pytest_cache/`, `.ruff_cache/`, `htmlcov/`, and `test-results/` remain
ignored build output. They are not source ownership and are recreated under the backend workflow as needed.

### Future frontend reservation

`frontend/` is reserved as a future product subtree. Ticket 71 creates no directory or frontend file. A future
frontend ticket must define its own metadata, lockfile, toolchain, and environment contract before implementation.

## Path-sensitive inventory

Every path-sensitive surface below must be updated with the move. The list records current ownership and required
translation so later tickets do not infer paths from stale root examples.

| Surface | Current references | Required Phase 10 treatment |
| --- | --- | --- |
| Operator commands | `pyproject.toml` Poe tasks, README setup commands, `docs/build/plan.md`, `docs/build/prerequisites.md` | Keep root entry points explicit; delegate Python work to `backend/` and document the working-directory contract |
| Python configuration | Ruff, mypy, ty, pytest, coverage, Django settings, and package discovery in `pyproject.toml` | Move configuration with backend metadata; retain root `.github/scripts` through explicit relative paths |
| Path resolution | `scripts/*.py`, `src/config/settings/*.py`, tests using `Path.parents`, `cwd`, `PYTHONPATH`, or repository-root constants | Define one repository-root contract and one backend-root contract; update environment, docs, Compose, and source paths deliberately |
| Docker build | `docker/django/Dockerfile`, `docker/django/entrypoint.sh`, `docker/pgbackrest/Dockerfile` | Keep Docker orchestration global; update build contexts and `COPY` paths while preserving normalized runtime paths where possible |
| Compose mounts | `compose.development.yaml`, `compose.testing.yaml`, shared Compose files | Keep root env files and registry names global; update backend source, scripts, and metadata mounts or build contexts |
| CI | `.github/PULL_REQUEST_TEMPLATE.md`, `.github/ISSUE_TEMPLATE/`, and `.github/scripts/validate_commit_message.py` | Keep GitHub policy global; invoke backend project explicitly and retain root policy script paths |
| Hooks | `.pre-commit-config.yaml`, commit-message hook, `.gitmessage` | Keep hooks global; run backend lint, types, tests, and documentation checks from explicit backend paths |
| Documentation | README, build plan, prerequisites, platform conventions, service inventory, ADRs, generated OpenAPI evidence | Update command examples and moved paths; preserve historical Phase 1–9 evidence with an explicit path-translation note |
| Generated artifacts | `docs/api/openapi-v1.yaml` evidence paths | Regenerate or update evidence paths after test relocation; do not change API behavior |

## Exact command and hook inventory

The Poe task names currently declared in `backend/pyproject.toml` are listed below. Ticket 73 must preserve these
names while changing their project root; Ticket 76 must make root invocation explicit:

```text
dev, help, setup, environments-setup, docker-audit, docker-clean-check, convention-audit,
convention-audit-development, convention-audit-testing, security-audit, security-audit-static,
security-audit-runtime, secrets-generate, secrets-decrypt, developer-access-export, development-build,
development-up, development-down, development-rebuild, development-reset, development-status,
development-health, development-logs, testing-build, testing-up, testing-down, testing-rebuild,
testing-reset, testing-status, testing-health, testing-logs, testing-test-container, testing-test-host,
testing-test-both, testing-registration-timing-stability, testing-integration-audit, testing-verify,
migrate, migrations, superuser, shell, django-check, openapi-generate, openapi-check, docs-standard,
lint, format, format-check, types-mypy, types-ty, typecheck, architecture-audit, test, test-stages,
test-serial, test-serial-stages, test-fresh, test-fresh-stages, test-unit, test-integration,
test-integration-stages, test-parallel, test-core, test-core-fresh, test-security-timing, check
```

The root hook inventory is:

- `.pre-commit-config.yaml` runs backend-scoped Ruff, documentation, type, and pytest tasks with
  `uv run --project backend poe -C backend ...`; the commit-message hook runs
  `uv run --project backend python .github/scripts/validate_commit_message.py`. These commands keep backend tooling
  explicit while `.github/scripts/` remains root-global policy.
- The same file runs root `.github/scripts/validate_commit_message.py` on the `commit-msg` stage. This path remains
  global policy and must not move with backend code.
- `.gitmessage` supplies the global commit template; commit-message validation remains root policy.
- `.editorconfig`, `.gitattributes`, `.gitignore`, `.markdownlint-cli2.jsonc`, `.markdownlint.json`, and
  `.yamllint.yml` apply to both current backend files and future frontend files.

## Exact path and import inventory

The following list records pre-move path calculations and subprocess import paths. Completed Tickets 74 and 75
translated these surfaces; current source paths live under `backend/` while normalized container paths remain `/app/src`
and `/app/scripts`:

- Scripts defining `REPOSITORY_ROOT` from `Path(__file__)`: `scripts/audit_naming.py`, `scripts/audit_security.py`,
  `scripts/check_docstrings.py`, `scripts/export_developer_access.py`, `scripts/gen_secrets.py`,
  `scripts/manage_platform.py`, `scripts/preflight.py`, `scripts/seed_storage.py`, and `scripts/sops_env.py`.
- Runtime settings path roots: `src/config/settings/__init__.py` resolves repository environment files, while
  `src/config/settings/base.py` resolves templates and static assets.
- Harness path roots: `tests/unit/test_harness.py`, `tests/unit/test_dependencies.py`,
  `tests/unit/test_architecture.py`, `tests/unit/compose/test_foundation.py`,
  `tests/unit/config/test_api_documentation.py`, `tests/unit/config/test_entrypoints.py`,
  `tests/unit/config/test_openapi_contract.py`, `tests/unit/notifications/test_protocol.py`,
  `tests/unit/scripts/test_django_entrypoint.py`, `tests/unit/scripts/test_pgbackrest_entrypoint.py`, and the
  integration configuration and notification tests resolve repository paths, working directories, or `PYTHONPATH`.
- Subprocess import paths: `scripts/manage_platform.py` sets host `PYTHONPATH` from `src` and repository root and
  emits `/app/src:/app` for containers. Integration tests in `tests/integration/notifications/` and
  `tests/integration/config/` build equivalent host paths.
- Package imports: Django modules import through the `config` package from `src`; tests import `config`, application
  packages, and script modules from the project root. The move must preserve package discovery and the documented
  `PYTHONPATH` contract.
- Generated contract path: the `openapi-generate` task runs `src/manage.py` and writes `docs/api/openapi-v1.yaml`;
  API documentation tests read that artifact and invoke the same management entry point.

The Docker and Compose path inventory is:

- `compose.development.yaml` builds `docker/pgbackrest/Dockerfile` at lines 69–71 and 142–144 and
  `docker/django/Dockerfile` at lines 636–638, 711–713, 763–765, and 798–800. The replica bootstrap bind at line
  120 uses `./scripts/pg_replica_bootstrap.sh`.
- `compose.testing.yaml` builds `docker/django/Dockerfile` at lines 208–210, binds root
  `./.env.development` and `./.env.testing` at lines 232–233, and probes `/app/src` at line 252.
- `docker/django/Dockerfile` copies `src`, `scripts`, `tests`, and `docs` at lines 37–51, normalizes those trees at
  lines 87–109, and sets `/app/src` in `PYTHONPATH`. `docker/django/entrypoint.sh` executes `/app/scripts/*` and
  `/app/src/manage.py` at lines 40–57.
- `docker/pgbackrest/Dockerfile` copies the pgBackRest entrypoint from the current root scripts directory. Ticket 75
  must change that source to `backend/scripts/` while preserving its image path.
- Compose runtime commands also import or execute backend scripts inline: `compose.testing.yaml:219` imports
  `scripts.prepare_broker`, and `compose.development.yaml:753` executes `scripts/celery_worker_health.py`. Ticket 75
  must translate both module and script paths while preserving service commands and health semantics.

The documentation path inventory is:

- Active command and setup references: `README.md`, `AGENTS.md`, `docs/build/plan.md`,
  `docs/build/prerequisites.md`, and `docs/platform/conventions.md`.
- Service and policy references: `docs/platform/service-inventory.md`, `docs/platform/documentation-standard.md`,
  `docs/security/security-audit.md`, `docs/architecture/solid-audit-plan.md`, and ADRs
  `docs/adr/0001-uvicorn-asgi-server.md`, `docs/adr/0008-celery-rabbitmq.md`, and
  `docs/adr/0016-accept-release-lag.md`.
- Historical or generated evidence requiring explicit translation notes: `docs/architecture/solid-findings.md`,
  `docs/handover/phase-8.md`, `docs/handover/phase-9.md`, and `docs/api/openapi-v1.yaml`.
- Compatibility and work tracking references: `docs/conventions.md`, `docs/service-inventory.md`, `.scratch/README.md`,
  and all phase ticket files that name `src/`, `tests/`, `scripts/`, or the root Python manifest.

Ticket 76 must search this complete list and record whether each path is updated, intentionally historical, or
generated after the move. No documentation path may silently retain a command that points at a moved backend file.

The inventory deliberately records path families where dozens of tests share one contract. Exact file references above
are the owning modules; `rg -n "REPOSITORY_ROOT|PYTHONPATH|cwd=|Path\\(__file__\\)" src scripts tests` is the repeatable
check for newly discovered instances during Tickets 73–77.

## Resolved ownership decisions

1. Environment files stay at root. `.sops.yaml` creation rules and Compose `env_file` paths already assume that
   boundary.
2. Docker orchestration stays at root. Dockerfiles may copy backend files, but service names, contexts, networks,
   volumes, and runtime boundaries remain global registry data.
3. `.github/scripts/` stays at root as shared CI policy. Backend tooling references it explicitly after metadata moves.
4. `docs/`, `.scratch/`, and repository policy stay at root. They govern both backend and future frontend work.
5. No history rewrite occurs. Completed Phase 1–9 commits remain unchanged; Phase 10 adds forward migration commits.

## Evidence and handoff

The inventory was checked against the root file listing and hidden-file repository search on 2026-10-04. No frontend
implementation exists. Tickets 72–78 own the subsequent environment, project, source, Docker, CI, documentation,
clean-checkout, runtime, and handover updates. Any discovered ownership conflict must update this inventory and ADR
0023 before moving a file.
