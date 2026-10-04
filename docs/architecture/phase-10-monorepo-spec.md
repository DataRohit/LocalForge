# Phase 10 monorepo specification

## Problem

LocalForge currently stores backend code, Python metadata, tests, scripts, and global deployment material in one flat
root. A future frontend will share the repository, so backend ownership must become explicit without moving global
environment or deployment concerns into a product subtree.

## Requirements

1. Keep one repository and one Git history.
2. Keep all environment and secret files global at repository root.
3. Move backend and Python-owned files into `backend/`.
4. Keep Compose orchestration and Docker service definitions global unless a ticket proves a backend-only file must
   move with its build context.
5. Add no frontend implementation.
6. Preserve existing commands through documented root entry points or make every intentional command-path change
   explicit in the Phase 10 handover.
7. Preserve two environments, pinned dependencies, offline runtime boundaries, quality gates, and secret rules.
8. Update agent instructions, docs, ticket indexes, CI, hooks, and path-sensitive tooling in the same phase.

## Acceptance evidence

- An ownership table and ADR describe every moved and retained class of file.
- A clean checkout creates `backend/.venv`, installs from `backend/pyproject.toml`, and finds `backend/uv.lock`.
- Root Compose workflows load root encrypted environment material and build the backend from its new context.
- Backend checks, tests, Docker audits, security audits, and runtime truth gates pass after the move.
- Repository search finds no stale path assumptions, duplicate Python project metadata, or accidental frontend files.
- Phase 10 handover records rollback, compatibility decisions, and remaining work.
