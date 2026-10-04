---
status: accepted
date: 2026-10-04
---

# Backend-first monorepo layout

LocalForge becomes a monorepo with one repository root and a reserved future frontend. Backend source, Python
metadata, the backend virtual environment, tests, and Python operator scripts live under `backend/`. Global
environment files, Compose orchestration, repository policy, documentation, and shared automation remain at the root.
No frontend is created in Phase 10.

## Layout contract

| Scope | Location | Rule |
| --- | --- | --- |
| Global environment and secrets | repository root | `.env.example`, `.env.*.sops`, `.sops.yaml`; Compose loads them from root |
| Backend Python project | `backend/` | `pyproject.toml`, `uv.lock`, `.python-version`, `.venv`, `src/`, `tests/`, `scripts/` |
| Docker orchestration | repository root | Compose files and Docker build contexts stay global |
| Shared repository policy | repository root | `AGENTS.md`, commit policy, hooks, `.gitignore`, `.gitattributes`, CI metadata |
| Product documentation | repository root | `docs/`, `CONTEXT.md`, and `.scratch/` remain global |
| Future frontend | reserved `frontend/` | directory is documented only; no files or toolchain are added in Phase 10 |

## Consequences

- Root commands delegate into `backend/` through explicit working-directory configuration; no command depends on the
  caller's current directory.
- Compose paths, Docker build contexts, CI paths, pre-commit paths, coverage paths, and documentation links must be
  updated together and verified from a clean checkout.
- Root environment names and secret ownership stay global. Backend code reads the same root env files through an
  explicit path contract.
- `backend/.venv` is local and ignored. It is never copied, encrypted, or committed.
- Phase 10 changes repository shape and command paths only. It does not add an environment, route, service, frontend,
  or dependency without a separate governing ticket.

## Considered options

**Move every file below `backend/`.** Rejected: Compose, environment, policy, documentation, and future frontend
ownership are repository-global concerns.

**Keep Python files at the root and add only `frontend/`.** Rejected: it leaves backend ownership ambiguous and does
not create the requested backend boundary.

**Create the frontend now.** Rejected: the user explicitly deferred frontend work.
