# AGENTS.md

Operating instructions for the agent building LocalForge, a full-stack project.

This file is the entry point and stays in context every turn, so it holds only what every run needs: the shape of
the work, the rules that are easy to break, and where to find the rest. It does not restate the documents it points
at.

## Start here, every session

1. **Reach for a skill before improvising.** Run **`/ask-matt`** whenever you are unsure which workflow fits — it
   routes across every skill in `.agents/skills/`. Do not invent a process a skill already defines.
    - Building a ticket → **`/implement`**, which drives **`/tdd`**. Run its closing diff review inline in the main
      thread, never through sub-agents.
    - Something is broken → **`/diagnosing-bugs`**.
    - Terminology, or recording a decision → **`/domain-modeling`**.
    - Writing or editing `AGENTS.md`, `CONTEXT.md`, or a skill → **`/writing-for-agents`**.
    - Designing a module's shape → **`/codebase-design`**.
2. **When in doubt, read [docs/](./docs/).** It is authoritative for every tool choice, name, port, variable, and
   phase gate in this platform. The map is at the bottom of this file. Guessing when `docs/` has the answer is the
   most expensive mistake available here — it produces work that has to be redone against the registry.
3. **Pick up work from the canonical [.scratch ticket index](./.scratch/README.md#ticket-index).** Resolve a ticket
   number through that exact link or a `NN-*.md` file search, then follow its blocker and governing-source links.
   Never synthesize a ticket filename from its title. Work the frontier: any ticket whose blockers are done. Keep
   one multi-ticket run in the same orchestrator thread so ticket evidence and sequencing remain intact.
4. **Treat [`.agents/`](./.agents/) as read-only.** Its skills are installed instructions, not project files.
   Agents may read and invoke them, but must never create, update, move, rename, or delete anything under
   `.agents/`. Put project-specific safeguards in `AGENTS.md`, `.scratch/`, `docs/`, or another project-owned path.
5. **Resolve repository paths before reading them.** Follow an exact link or path when one exists. When only a
   number, title, symbol, or description is available, discover the path with repository search before opening it.
   Never turn prose into a guessed filename, directory, symbol, command, variable, service name, or configuration
   key. If a lookup fails, search the repository instead of trying another invented variant.

## Agent topology

One agent does the work: the thread receiving the user's messages. It owns repository discovery, implementation,
investigation, remediation, commands, verification, documentation, sequencing, and reporting. No audit agents, review
agents, or delegated coding agents run in this repository.

A ticket is done when the main thread has verified it itself:

1. Walk every acceptance criterion and mark it only with evidence from the repository or a command run in `testing`.
2. Rerun every gate the change affects, including the runtime-truth evidence in rule 9.
3. Review the diff once against the ticket's criteria and the repository standards, inline, and remediate each
   legitimate finding before rerunning the affected gates.
4. Record the verification in the ticket: commands, window, findings, and disposition.

Commit only under rule 10.

## Project

LocalForge is a **full-stack project** ([ADR 0024](./docs/adr/0024-full-stack-pivot.md)): a Django backend under
`backend/` and a React and Tailwind CSS single-page frontend under `frontend/`, on a **local, fully offline,
containerized platform** — database, cache, broker, WebSocket message layer, object storage, mail capture, reverse
proxy, monitoring, logging, backups, read replication — running in Docker on one machine with no cloud dependency.

The backend serves a deliberately bounded application surface, and the frontend consumes it. The frontend is React
and Tailwind CSS only, a static single-page application with no server-side rendering and no backend-for-frontend
([ADR 0026](./docs/adr/0026-react-and-tailwind-frontend.md)). The backend stays the authority for validation,
authorization, and state.

## Environments

Exactly two, and there is no third.

| Environment   | Purpose                         | Compose project   | Classification during full-stack work    |
| ------------- | ------------------------------- | ----------------- | ---------------------------------------- |
| `development` | Full stack, every dashboard     | `localforge-dev`  | **Live.** Serves the public deployment   |
| `testing`     | Headless subset the suite needs | `localforge-test` | Isolated work; never reaches development |

`development` takes no read or write — containers, volumes, networks, `.env.development`, bind-mounted files —
without the user's explicit permission. Prepare changes for it as inert candidate files. Before running any
environment-sensitive command, confirm from its Poe task or Make target that it addresses `testing` only.

`testing` must pass **in a container and on the host**. One passing is not a pass.

## Rules

1. All runtime is Docker; Compose drives both environments.
2. The stack runs with no internet access. Only image pulls, image builds, and dependency resolution touch the
   network, once.
3. Application and agent traffic stays local by default. The only approved external boundaries are the documented
   Phase 9 Cloudflare Tunnel public edge and Resend development email; use no other hosted gateways, telemetry,
   dashboards, or external SaaS integrations.
4. Every service is integrated with Django and verified end to end before the next one starts.
5. Kubernetes is reasoning-only. Create no cluster, write no manifests, apply nothing.
6. Pinned versions only. `latest` is forbidden, including Dockerfile base images.
7. Use `uv run` for every Python command. Bare `python` here is 3.12.10, not the required 3.14.6.
   Root operator commands use `make`; the root `Makefile` selects `backend/` after Ticket 73 moves the project and
   runs Poe from the selected project. Frontend commands run through root `make frontend-*` targets; the package
   manager is pnpm, installed with `pnpm install --frozen-lockfile`, and dependencies change only through
   `pnpm add --save-exact` or `pnpm remove`. Never use npm in `frontend/`.
8. Keep the quality gate where it is: 100% branch coverage, Ruff `select = ["ALL"]`, mypy `strict`. Fix the code.
9. **Runtime truth is a separate gate.** Tests prove controlled cases; they do not prove the running stack is
   healthy. After any runtime, integration, infrastructure, observability, or test-harness change:
    - Start or rebuild every affected environment through its supported Poe command.
    - Require environment health and the project-scoped Docker audit to pass.
    - Exercise the changed behavior through its deployed public or operator seam.
    - Inspect a bounded post-start/post-exercise log window for every affected container.
    - Treat any unexplained `WARNING`, `ERROR`, or `CRITICAL`, restart, unhealthy state, missing service, or
      success response logged as failure as a blocker.
   A passing test suite never overrides contradictory live health or log evidence. Every ticket's verification record
   lists the exact health, behavior, log commands, time window, findings, and disposition.
   While `development` is live, this rule runs against `testing` only. Development runtime truth stays an explicit
   unverified gate until the user authorizes inspection, and reported status never exceeds **Locally Verified**
   (the ladder is Implemented, Locally Verified, Production-Ready, Production-Deployed).
10. **Commits are the user's call.** Make no git commit, amend, or push until the user says "You may commit." This
    overrides `/implement` and the per-ticket commit step. The permission covers the work the user named, not later
    work.

## Scope

In scope: the infrastructure, its configuration, the application surface below, the frontend that consumes it, and
tests for all of it.

**The application surface is fixed.** These routes and no others:

| Group        | Routes                                                                                                                                                                                                                       |
| ------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Health       | `/health/` — what the load balancer polls                                                                                                                                                                                    |
| Schema       | the OpenAPI document, Swagger UI, ReDoc                                                                                                                                                                                      |
| Accounts     | `/users/`, `/users/me/`, `/users/resend_activation/`, `/users/set_password/`, `/users/reset_password/`, `/users/reset_password_confirm/`, `/users/set_username/`, `/users/reset_username/`, `/users/reset_username_confirm/` |
| Token auth   | `/token/login/`, `/token/logout/`                                                                                                                                                                                            |
| JWT auth     | `/jwt/create/`, `/jwt/refresh/`, `/jwt/verify/`                                                                                                                                                                              |
| Browser auth | `/browser/csrf/`, `/browser/login/`, `/browser/refresh/`, `/browser/logout/` — added by Phase 11, see [ADR 0025](./docs/adr/0025-browser-authentication.md)                                                                  |
| WebSocket    | the authenticated notification socket                                                                                                                                                                                        |
| Frontend     | the single-page application, every path outside `/api/`, `/ws/`, `/health/`, `/admin/`, `/static/` — see [ADR 0027](./docs/adr/0027-frontend-hosting-and-edge-routing.md)                                                    |

Every one of these documents **every status code it can return**, with a response example for each — including
codes raised by middleware, content negotiation, throttling, and CSRF, not only those raised in view code. See
[docs/adr/0018-api-error-contract.md](./docs/adr/0018-api-error-contract.md).

Out of scope, stated positively so the boundary is unambiguous:

- **Two environments only.** `development` and `testing`. No third environment, and the frontend adds none.
- **No route beyond the table above.** Adding one is a documentation change first.
- **No frontend server runtime.** No server-side rendering, no backend-for-frontend, no hosted runtime dependency,
  external authentication provider, telemetry, or CDN, and no UI or CSS framework other than React and Tailwind CSS.
- **One container per service.** Replica counts belong to an orchestrator; see
  [docs/platform/kubernetes-mapping.md](./docs/platform/kubernetes-mapping.md) Section 1 for the full list of
  scaling logic that stays out of the code.
- **Decided tool choices.** Settled in [docs/adr/](./docs/adr/README.md). To change one, update its ADR with dated
  primary-source evidence first.

## Code standards

**Write no comments.** Every explanation goes in a docstring, which is reachable at runtime, extracted by tooling,
and shown at the call site. Docstrings are structured at three levels:

| Level             | Required sections                                                                  |
| ----------------- | ---------------------------------------------------------------------------------- |
| File              | One-line title, then a 2–3 line description                                        |
| Class             | One-line title, 2–3 line description, what it inherits, its attributes and members |
| Function / method | One-line title, 2–3 line description, arguments, returns, raises                   |

This applies to application code and tests alike, and is enforced by the linter and a checker in the quality gate.
The reasoning is in
[docs/adr/0020-no-comments-structured-docstrings.md](./docs/adr/0020-no-comments-structured-docstrings.md).

**Frontend code follows [docs/platform/frontend-standard.md](./docs/platform/frontend-standard.md)** — layout,
components, Tailwind, accessibility, state ownership, API access — and the same no-comments rule, with TSDoc blocks.

**Tests run in parallel.** Every suite must pass under the parallel runner, and every test that touches a service
carries a timeout — a suite that hangs is a defect, not an inconvenience. A test needing serial execution is marked
and justifies itself in its docstring.

## Naming

Every container, volume, and network name is **already assigned** in
[docs/platform/conventions.md](./docs/platform/conventions.md) Section 2.

**Copy them verbatim.** They were chosen once, on 2026-09-13, and audits, scripts, and every other document
reference them. Do not generate new IDs, renumber, or swap a code for one that reads better — they are arbitrary by
design.

Format is `<service-type>-<short-unique-id>`, the ID being five characters from `a–z` and `2–9`. Adding a service
means adding a registry row in a documentation change first.

## Secrets

1. One env file per environment, loaded through `env_file:`. **Nothing is hardcoded inline in Compose** —
   `environment:` silently overrides `env_file:`, so an inline literal wins over the generated value.
2. `backend/scripts/gen_secrets.py` is the only source of secret values. Never invent one, type one, or paste one into a
   document, commit message, or log. Documentation uses `<GENERATED>`.
3. Committed: `.env.example` with placeholders, and the age-encrypted `.env.*.sops` files. Nothing else.
4. `detect-private-key` is an active pre-commit hook. Leave it enabled.
5. One distinct credential per service.

## Phase order

Twelve sequential ticket phases with explicit gates, in [docs/build/plan.md](./docs/build/plan.md) Section 3 and
the 99-ticket index in [.scratch/](./.scratch/README.md):

| Ticket phase | Delivers                                                                                 |
| ------------ | ---------------------------------------------------------------------------------------- |
| 1            | Infrastructure: every development service running and healthy                            |
| 2            | Project foundation: dependencies, settings split, standards, test layout, user model     |
| 3            | Integration: Django wired to every service, `/health` live behind the proxy              |
| 4            | REST API: the fixed route surface with every status code documented                      |
| 5            | WebSockets: authenticated sockets over the channel layer                                 |
| 6            | Async services: worker, scheduler, dashboard, async email, event fan-out                 |
| 7            | Testing and audit: both modes in parallel, convention and security audits                |
| 8            | SOLID architecture: evidence-led audit, focused improvements, final verification         |
| 9            | Public edge/email: Cloudflare, Resend, DNS, runtime verification                         |
| 10           | Monorepo restructure: global root contract, backend subtree, clean-checkout gate         |
| 11           | Backend freeze: test isolation, browser cookie auth, contract gaps, user-accepted freeze |
| 12           | Frontend: React and Tailwind app, quality gates, container and edge routing, rollback    |

Infrastructure comes first, then the project is brought up to meet it, then Django is wired to each running
service, then the application is built on top. Do not build the application against services that are not yet
verified healthy. The frontend starts only after the user accepts the Phase 11 backend freeze; backend code changes
after that reopen Phase 11. The Phase 11 and 12 plans are locked (2026-10-09): changing their scope needs an ADR
update and the user's approval.

**A phase may not begin until the previous gate passes.** If a gate fails, stop and fix it. Weakening a gate to make
it pass is the one move that is never available.

## Four reversed defaults

Verified 2026-09-13. If instinct says otherwise, the instinct is stale.

1. **MinIO is unusable.** Repository archived, Docker Hub image returns 404, Console removed from the AGPL server.
   Object storage is **SeaweedFS**.
2. **Promtail is end of life** (2026-03-02) and its source is gone from the Loki repository. Log collection is
   **Grafana Alloy**.
3. **Djoser is not adopted**, even though the account endpoints are exactly its surface. Its CI has no Django 6.0
   row, no Python 3.14 row, and pins DRF 3.14, and the schema generator cannot document it without a per-view
   override. The endpoints are first-party. See
   [docs/adr/0017-first-party-account-endpoints.md](./docs/adr/0017-first-party-account-endpoints.md).
4. **A PyPI trove classifier is not evidence.** It is a hand-edited string that drifts. When the question is "does
   this support Python 3.14 or Django 6.0", read the project's **CI matrix on its default branch**. Applying that
   standard reversed four conclusions — see
   [docs/adr/0016-accept-release-lag.md](./docs/adr/0016-accept-release-lag.md).

Smaller ones: Valkey replaces Redis on licence grounds, **RedisInsight must not be added** as its dashboard (SSPL,
no Valkey support), and **plain `channels` does not pull in Daphne** — it is an optional extra, and this platform
serves under Uvicorn without it.

## Repository conventions

These predate this work.

| Area         | Rule                                                                                        |
| ------------ | ------------------------------------------------------------------------------------------- |
| Dependencies | `uv` with `[dependency-groups]`; use `uv add`. **Never create a `requirements.txt`**        |
| Tasks        | `poethepoet`; full gate: `make check`                                                       |
| Linting      | Ruff, `select = ["ALL"]`, line length 100                                                   |
| Types        | mypy `strict` plus `ty`; both must pass                                                     |
| Tests        | pytest, 100% branch coverage enforced, `xfail_strict`, markers `unit` and `integration`     |
| Frontend     | `pnpm` with exact pins, committed lockfile; ESLint, Prettier, `tsc`, Vitest, Playwright     |
| Markdown     | markdownlint-cli2, 120-character lines outside tables and code                              |
| Line endings | LF, enforced by pre-commit                                                                  |
| Commits      | `COMMIT_CONVENTION.md`; a `commit-msg` hook validates the format                            |
| `.agents/`   | Read-only installed skills; never create, update, move, rename, or delete them              |
| ADRs         | `docs/adr/NNNN-slug.md`, per `.agents/skills/domain-modeling/ADR-FORMAT.md`                 |

## Where things live

| Question                                                      | File                                                                                                                                           |
| ------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| Which workflow or skill fits this task                        | **`/ask-matt`**, the router over `.agents/skills/`                                                                                             |
| What do the words mean                                        | [CONTEXT.md](./CONTEXT.md)                                                                                                                     |
| What should I work on next                                    | [.scratch/README.md](./.scratch/README.md) — 99 tickets in 12 sequential phases                                                                |
| Which tool, and why that one                                  | [docs/adr/](./docs/adr/README.md) — one decision per file, indexed                                                                             |
| How SOLID applies to this Python project                      | [docs/architecture/solid-audit-plan.md](./docs/architecture/solid-audit-plan.md)                                                               |
| Names, IDs, variables, secrets, script contracts              | [docs/platform/conventions.md](./docs/platform/conventions.md)                                                                                 |
| What a docstring must contain, and which comments are allowed | [docs/platform/documentation-standard.md](./docs/platform/documentation-standard.md)                                                           |
| Ports, startup order, health checks, dashboards, audits       | [docs/platform/service-inventory.md](./docs/platform/service-inventory.md)                                                                     |
| How this becomes Kubernetes later                             | [docs/platform/kubernetes-mapping.md](./docs/platform/kubernetes-mapping.md)                                                                   |
| What must be installed first                                  | [docs/build/prerequisites.md](./docs/build/prerequisites.md)                                                                                   |
| What to run, in what order                                    | [docs/build/plan.md](./docs/build/plan.md)                                                                                                     |
| Monorepo ownership                                            | [docs/architecture/phase-10-monorepo.md](./docs/architecture/phase-10-monorepo.md)                                                             |
| Phase 11 and 12 scope, contracts, quality bars, gates         | [phase-11-backend-freeze.md](./docs/architecture/phase-11-backend-freeze.md), [phase-12-frontend.md](./docs/architecture/phase-12-frontend.md) |
| Frontend rules: layout, Tailwind, accessibility, state, API   | [docs/platform/frontend-standard.md](./docs/platform/frontend-standard.md)                                                                     |
| Why full-stack, browser auth, edge routing, frontend tools    | ADRs [0024](./docs/adr/0024-full-stack-pivot.md)–[0028](./docs/adr/0028-frontend-toolchain.md)                                                 |

When two documents disagree, the more specific wins: `conventions.md` for a name, `service-inventory.md` for a port.
Fix the disagreement rather than choosing silently.

## When unsure

- A tool looks stale: check its CI matrix and packaging metadata, and record the evidence with today's date in its
  ADR.
- A document seems wrong: fix it in the same change as the code.
- A step is ambiguous: prefer the reading that keeps the platform offline, pinned, and named to the registry.
- About to create a file not in [docs/build/plan.md](./docs/build/plan.md) Section 2: add it there with a reason
  first.
