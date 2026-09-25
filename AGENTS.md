# AGENTS.md

Operating instructions for the agent building the local backend platform.

This file is the entry point and stays in context every turn, so it holds only what every run needs: the shape of
the work, the rules that are easy to break, and where to find the rest. It does not restate the documents it points
at.

## Start here, every session

1. **Reach for a skill before improvising.** Run **`/ask-matt`** whenever you are unsure which workflow fits — it
   routes across every skill in `.agents/skills/`. Do not invent a process a skill already defines.
    - Building a ticket → **`/implement`**, which drives **`/tdd`** and closes with **`/code-review`**.
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

The thread receiving the user's messages is the **orchestrator and coder**. It owns all repository discovery,
implementation, investigation, remediation, commands, verification, documentation, commits, sequencing, and
reporting. Never launch or use a subagent for coding or any repository mutation.

Use subagents only for independent, read-only ticket audits after the main thread has completed the ticket,
passed its verification, and prepared a complete audit package:

1. After the first package exists, launch exactly two audit agents concurrently and retain both IDs for the whole
   run: one **GPT-5.6 Terra** (`gpt-5.6-terra`) and one standard **GPT-5.6 Sol** (`gpt-5.6-sol`), both with
   `reasoning_effort: high` and `context_tier: long_context`.
2. Send both auditors the identical complete package. They review the entire ticket, remain read-only, make no
   edits, and do not communicate with each other.
3. The main thread validates every finding against repository evidence, remediates every legitimate finding, and
   reruns affected gates.
4. When remediation materially changes behavior, security, routing, protocol, payloads, close codes, or integration,
   resume the same two auditors concurrently with the revised identical package. Repeat until both reports contain
   no legitimate findings.
5. The main thread creates the ticket's single commit only after the audit loop is clean, then implements the next
   ticket while the retained auditors stay idle.

Never run audit agents while the main thread is changing the repository. Replace a permanently failed auditor once
and retain the replacement for the remainder of the run. All audit agents are OpenAI-only. Never use a Fast or
non-OpenAI model.

## Project

LocalForge is a Django project. This work builds a **local, fully offline, containerized backend platform** around
it — database, cache, broker, WebSocket message layer, object storage, mail capture, reverse proxy, monitoring,
logging, backups, read replication — running in Docker on one machine with no cloud dependency.

On top of that platform sits a deliberately bounded application surface: an authenticated REST API, WebSockets, and
a health endpoint for the load balancer. Nothing else.

## Environments

Exactly two, and there is no third.

| Environment   | Purpose                         | Compose project   |
| ------------- | ------------------------------- | ----------------- |
| `development` | Full stack, every dashboard     | `localforge-dev`  |
| `testing`     | Headless subset the suite needs | `localforge-test` |

`testing` must pass **in a container and on the host**. One passing is not a pass.

## Rules

1. All runtime is Docker; Compose drives both environments.
2. The stack runs with no internet access. Only image pulls, image builds, and dependency resolution touch the
   network, once.
3. Application and agent traffic stays local by default. The only approved external boundaries are the documented
   Phase 10 Cloudflare Tunnel public edge and Resend development email; use no other hosted gateways, telemetry,
   dashboards, or external SaaS integrations.
4. Every service is integrated with Django and verified end to end before the next one starts.
5. Kubernetes is reasoning-only. Create no cluster, write no manifests, apply nothing.
6. Pinned versions only. `latest` is forbidden, including Dockerfile base images.
7. Use `uv run` for every Python command. Bare `python` here is 3.12.10, not the required 3.14.6.
8. Keep the quality gate where it is: 100% branch coverage, Ruff `select = ["ALL"]`, mypy `strict`. Fix the code.
9. **Runtime truth is a separate gate.** Tests prove controlled cases; they do not prove the running stack is
   healthy. After any runtime, integration, infrastructure, observability, or test-harness change:
    - Start or rebuild every affected environment through its supported Poe command.
    - Require environment health and the project-scoped Docker audit to pass.
    - Exercise the changed behavior through its deployed public or operator seam.
    - Inspect a bounded post-start/post-exercise log window for every affected container.
    - Treat any unexplained `WARNING`, `ERROR`, or `CRITICAL`, restart, unhealthy state, missing service, or
      success response logged as failure as a blocker.
   A passing test suite never overrides contradictory live health or log evidence. Every audit package records the
   exact health, behavior, log commands, time window, findings, and disposition.

## Scope

In scope: the infrastructure, its configuration, the application surface below, and tests for all of it.

**The application surface is fixed.** These routes and no others:

| Group      | Routes                                                                                                                                                                                                                       |
| ---------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Health     | `/health/` — what the load balancer polls                                                                                                                                                                                    |
| Schema     | the OpenAPI document, Swagger UI, ReDoc                                                                                                                                                                                      |
| Accounts   | `/users/`, `/users/me/`, `/users/resend_activation/`, `/users/set_password/`, `/users/reset_password/`, `/users/reset_password_confirm/`, `/users/set_username/`, `/users/reset_username/`, `/users/reset_username_confirm/` |
| Token auth | `/token/login/`, `/token/logout/`                                                                                                                                                                                            |
| JWT auth   | `/jwt/create/`, `/jwt/refresh/`, `/jwt/verify/`                                                                                                                                                                              |
| WebSocket  | the authenticated notification socket                                                                                                                                                                                        |

Every one of these documents **every status code it can return**, with a response example for each — including
codes raised by middleware, content negotiation, throttling, and CSRF, not only those raised in view code. See
[docs/adr/0018-api-error-contract.md](./docs/adr/0018-api-error-contract.md).

Out of scope, stated positively so the boundary is unambiguous:

- **Two environments only.** `development` and `testing`. No production environment.
- **No route beyond the table above.** Adding one is a documentation change first.
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
2. `scripts/gen_secrets.py` is the only source of secret values. Never invent one, type one, or paste one into a
   document, commit message, or log. Documentation uses `<GENERATED>`.
3. Committed: `.env.example` with placeholders, and the age-encrypted `.env.*.sops` files. Nothing else.
4. `detect-private-key` is an active pre-commit hook. Leave it enabled.
5. One distinct credential per service.

## Phase order

Nine build-plan phases with explicit gates, in [docs/build/plan.md](./docs/build/plan.md) Section 3, delivered by
the 63 tickets in [.scratch/](./.scratch/README.md), grouped into 8 ticket phases:

| Ticket phase | Delivers                                                                             |
| ------------ | ------------------------------------------------------------------------------------ |
| 1            | Infrastructure: every development service running and healthy                        |
| 2            | Project foundation: dependencies, settings split, standards, test layout, user model |
| 3            | Integration: Django wired to every service, `/health` live behind the proxy          |
| 4            | REST API: the fixed route surface with every status code documented                  |
| 5            | WebSockets: authenticated sockets over the channel layer                             |
| 6            | Async services: worker, scheduler, dashboard, async email, event fan-out             |
| 7            | Testing and audit: both modes in parallel, convention and security audits            |
| 8            | SOLID architecture: evidence-led audit, focused improvements, final verification     |

Infrastructure comes first, then the project is brought up to meet it, then Django is wired to each running
service, then the application is built on top. Do not build the application against services that are not yet
verified healthy.

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

| Area         | Rule                                                                                      |
| ------------ | ----------------------------------------------------------------------------------------- |
| Dependencies | `uv` with `[dependency-groups]`; use `uv add`. **Never create a `requirements.txt`**      |
| Tasks        | `poethepoet`; `uv run poe check` is the full local gate                                   |
| Linting      | Ruff, `select = ["ALL"]`, line length 100                                                 |
| Types        | mypy `strict` plus `ty`; both must pass                                                   |
| Tests        | pytest, 100% branch coverage enforced, `xfail_strict`, markers `unit` and `integration`   |
| Markdown     | markdownlint-cli2, 120-character lines outside tables and code                            |
| Line endings | LF, enforced by pre-commit                                                                |
| Commits      | `COMMIT_CONVENTION.md`; a `commit-msg` hook validates the format                          |
| `.agents/`   | Read-only installed skills; never create, update, move, rename, or delete them            |
| ADRs         | `docs/adr/NNNN-slug.md`, per `.agents/skills/domain-modeling/ADR-FORMAT.md`               |

## Where things live

| Question                                                      | File                                                                                 |
| ------------------------------------------------------------- | ------------------------------------------------------------------------------------ |
| Which workflow or skill fits this task                        | **`/ask-matt`**, the router over `.agents/skills/`                                   |
| What do the words mean                                        | [CONTEXT.md](./CONTEXT.md)                                                           |
| What should I work on next                                    | [.scratch/README.md](./.scratch/README.md) — 63 tickets, phased, with blockers       |
| Which tool, and why that one                                  | [docs/adr/](./docs/adr/README.md) — one decision per file, indexed                   |
| How SOLID applies to this Python project                      | [docs/architecture/solid-audit-plan.md](./docs/architecture/solid-audit-plan.md)     |
| Names, IDs, variables, secrets, script contracts              | [docs/platform/conventions.md](./docs/platform/conventions.md)                       |
| What a docstring must contain, and which comments are allowed | [docs/platform/documentation-standard.md](./docs/platform/documentation-standard.md) |
| Ports, startup order, health checks, dashboards, audits       | [docs/platform/service-inventory.md](./docs/platform/service-inventory.md)           |
| How this becomes Kubernetes later                             | [docs/platform/kubernetes-mapping.md](./docs/platform/kubernetes-mapping.md)         |
| What must be installed first                                  | [docs/build/prerequisites.md](./docs/build/prerequisites.md)                         |
| What to run, in what order                                    | [docs/build/plan.md](./docs/build/plan.md)                                           |

When two documents disagree, the more specific wins: `conventions.md` for a name, `service-inventory.md` for a port.
Fix the disagreement rather than choosing silently.

## When unsure

- A tool looks stale: check its CI matrix and packaging metadata, and record the evidence with today's date in its
  ADR.
- A document seems wrong: fix it in the same change as the code.
- A step is ambiguous: prefer the reading that keeps the platform offline, pinned, and named to the registry.
- About to create a file not in [docs/build/plan.md](./docs/build/plan.md) Section 2: add it there with a reason
  first.
