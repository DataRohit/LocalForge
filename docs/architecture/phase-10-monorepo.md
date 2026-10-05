# Phase 10: Backend-first monorepo restructure

Phase 10 moves the existing Python backend into `backend/` while preserving one repository-wide Compose deployment,
global environment files, global documentation, and global repository policy. It prepares a future `frontend/`
directory without creating frontend code or a second environment.

The pre-move ownership and path-sensitive inventory is
[phase-10-monorepo-inventory.md](./phase-10-monorepo-inventory.md).

## Scope

- Define and document the root versus `backend/` ownership boundary.
- Move Python metadata, virtual-environment configuration, backend source, tests, and Python scripts under `backend/`.
- Keep `.env.example`, encrypted environment files, `.sops.yaml`, Compose files, Docker orchestration, `docs/`,
  `.scratch/`, `AGENTS.md`, and repository policy at root.
- Provide `localforge.ps1` and `localforge.sh` as root entry points. They resolve the repository root, select the
  backend project when present, and invoke Poe from that project directory regardless of caller directory.
- Update every command, Docker context, CI workflow, hook, import path, coverage path, and documentation link that
  references moved backend files.
- Preserve the two existing Compose environments and every registered name, port, route, secret, and ticket history.
- Prove a clean checkout can install, build, run, test, audit, and decrypt environments using the new layout.

## Out of scope

- No frontend files, framework, package manager, or frontend environment.
- No production environment or third Compose project.
- No route, service, secret, dependency, or public edge change.
- No rewrite of completed Phase 1–9 history.

## Target ownership

| Root remains global | Backend moves under `backend/` |
| --- | --- |
| `.env.example`, `.env.development.sops`, `.env.testing.sops`, `.sops.yaml` | `backend/pyproject.toml`, `backend/uv.lock`, `backend/.python-version` |
| `compose*.yaml`, `docker/` | `backend/.venv`, `backend/src/`, `backend/tests/`, `backend/scripts/` |
| `AGENTS.md`, `CONTEXT.md`, `docs/`, `.scratch/` | Python test and coverage configuration |
| Git policy, hooks, license, governance, CI coordination | Backend-only Python tooling and package metadata |

## Phase gate

The gate passes when all Phase 10 tickets are done, root and backend ownership match this document, no stale root
backend path remains, root environment and Compose workflows still start both existing environments, backend quality
and runtime gates pass, encrypted environments remain decryptable, and the final handover records the exact commands
and clean-checkout evidence.
