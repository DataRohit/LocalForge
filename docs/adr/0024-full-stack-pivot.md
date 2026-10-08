---
status: accepted
date: 2026-10-08
---

# LocalForge becomes a full-stack project

LocalForge is a full-stack application: the Django backend and its container platform under `backend/` and the
root, plus a React and Tailwind CSS single-page frontend under `frontend/`. The user mandated the pivot on
2026-10-08. It supersedes the "backend platform only" and "no frontend" statements in
[0023](./0023-monorepo-layout.md), the Phase 10 documents, and the original scope of `AGENTS.md`.

## What changes and what does not

| Stays fixed | Changes |
| --- | --- |
| Exactly two environments, `development` and `testing`; no third | `frontend/` becomes a live product subtree |
| Pinned versions, offline runtime, one container per service | The application surface gains the browser-auth group and the SPA |
| Backend is the authority for validation, authorization, and state | Phase 11 freezes the backend for browser use; Phase 12 builds the frontend against it |
| Names come from the registry; secrets from `gen_secrets.py` | One new registry row: the frontend container |

## Environment classification

During the full-stack work `development` (`localforge-dev`) is **live**: it serves the public deployment through
the Phase 9 edge. No read or write against its containers, volumes, networks, or env file happens without explicit
user permission, and every change that would reach it is prepared as an inert candidate. `testing`
(`localforge-test`) is the isolated working environment and must never reach development credentials, volumes, or
networks. A passing `testing` run is **Locally Verified**, never production verification.

## Notifications scope

The notification socket has no REST history endpoint. The frontend shows notifications received while connected and
an explicit empty state otherwise. Adding a history route is a separate documentation-first decision, not part of
this pivot.

## Considered options

**Keep the repository backend-only and build the frontend elsewhere.** Rejected: the user wants one repository with
one governing document set, one environment contract, and one edge.

**Add a third environment for the frontend.** Rejected: it would duplicate secrets and Compose ownership that
[0023](./0023-monorepo-layout.md) deliberately keeps global.

## Consequences

- `AGENTS.md`, `README.md`, `CONTEXT.md`, and the plan describe a full-stack project; Phase 10 records stay as
  historical evidence and point here.
- `AGENTS.md` rule 9 (runtime truth) applies to `testing` only until the user authorizes a `development` inspection.
- Git commits require the user's explicit "You may commit."
