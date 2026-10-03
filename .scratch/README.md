# LocalForge build tickets

70 tracer-bullet tickets across 9 ticket phases, numbered globally in dependency order. Each ticket is sized to fit one
fresh context window and is verifiable on its own.

Format follows [the ticket-writing skill](../.agents/skills/to-tickets/SKILL.md). Ticket files avoid implementation
paths and code snippets because those go stale. Blockers and governing sources are exact links: navigation must be
resolved from the repository, never reconstructed from a ticket title.

## How to work these

1. Resolve the ticket through the [ticket index](#ticket-index), not by inventing a filename from its title.
2. Work the **frontier**: any ticket whose linked blockers are all done.
3. Read every linked governing source before starting. [AGENTS.md](../AGENTS.md) defines repository-wide rules.
4. `/clear` context between tickets. Each is self-contained by construction.
5. A ticket is done when every acceptance criterion is checked, `uv run poe check` is green, and the
   [runtime truth gate](../AGENTS.md#rules) passes for every affected environment and container. Unexplained live
   warnings or errors block completion even when tests pass.

Every ticket uses the same structure: `What to build`, a list-form `Blocked by` section, `Governing sources`, a
`Status` field, and checked acceptance criteria. `done` means every criterion and gate passed; `pending` means at
least one criterion or gate remains. Evidence paragraphs may follow the shared fields when a ticket records an audit
or external verification result.

## Phase order

Infrastructure first, then the project is brought up to meet it, then Django is wired to each running service, then
the application surface is built on top.

| Phase | Folder | Tickets | Delivers |
| --- | --- | --- | --- |
| 1 | [phase-1-infrastructure](./phase-1-infrastructure) | 01–12 | Every development service running and healthy in Docker |
| 2 | [phase-2-project-foundation](./phase-2-project-foundation) | 13–18 | Dependencies, settings split, standards, test layout, user model |
| 3 | [phase-3-infrastructure-integration](./phase-3-infrastructure-integration) | 19–26 | Django talking to every service, verified end to end, `/health` live |
| 4 | [phase-4-rest-api](./phase-4-rest-api) | 27–37 | The full REST surface with every status code documented |
| 5 | [phase-5-websockets](./phase-5-websockets) | 38–41 | Authenticated WebSockets over the Channels layer |
| 6 | [phase-6-async-services](./phase-6-async-services) | 42–46 | Celery worker, scheduler, Flower, async email, event fan-out |
| 7 | [phase-7-testing-and-audit](./phase-7-testing-and-audit) | 47–52 | Full suites in both modes, convention and security audits |
| 8 | [phase-8-solid-architecture](./phase-8-solid-architecture) | 53–63 | Evidence-led SOLID audit, focused improvements, final verification |
| 9 | [phase-9-public-edge-email](./phase-9-public-edge-email) | 64–70 | Cloudflare Tunnel edge, Resend development email, DNS security, public runtime verification |

## Ticket index

This is the canonical number-to-file mapping. A ticket number resolves through this table or an exact `NN-*.md`
search; its title is never used to predict its filename.

| Ticket | Exact file |
| --- | --- |
| 01 | [01: Prerequisite gate and preflight script](./phase-1-infrastructure/01-preflight-prerequisite-gate.md) |
| 02 | [02: Secret generation and per-environment env files](./phase-1-infrastructure/02-secret-generation-env-files.md) |
| 03 | [03: Compose foundation with explicit networks and volumes](./phase-1-infrastructure/03-compose-foundation-networks-volumes.md) |
| 04 | [04: PostgreSQL primary and streaming standby](./phase-1-infrastructure/04-postgresql-primary-standby.md) |
| 05 | [05: Automated database backup with verified restore](./phase-1-infrastructure/05-database-backup-and-restore-drill.md) |
| 06 | [06: Cache and channel layer on two isolated Valkey instances](./phase-1-infrastructure/06-valkey-cache-and-channel-layer.md) |
| 07 | [07: Message broker for background work](./phase-1-infrastructure/07-message-broker.md) |
| 08 | [08: S3-compatible object storage](./phase-1-infrastructure/08-object-storage.md) |
| 09 | [09: SMTP capture for local mail](./phase-1-infrastructure/09-smtp-capture.md) |
| 10 | [10: Edge reverse proxy with a secured dashboard](./phase-1-infrastructure/10-edge-reverse-proxy.md) |
| 11 | [11: Metrics and log aggregation with provisioned dashboards](./phase-1-infrastructure/11-metrics-and-log-aggregation.md) |
| 12 | [12: Database administration dashboard](./phase-1-infrastructure/12-database-admin-dashboard.md) |
| 13 | [13: Dependency baseline](./phase-2-project-foundation/13-dependency-baseline.md) |
| 14 | [14: Environment-aware settings package](./phase-2-project-foundation/14-settings-package-split.md) |
| 15 | [15: Documentation and comment standards, enforced](./phase-2-project-foundation/15-documentation-standards-enforcement.md) |
| 16 | [16: Test layout and parallel execution](./phase-2-project-foundation/16-test-layout-and-parallelism.md) |
| 17 | [17: Application container image and entrypoint](./phase-2-project-foundation/17-application-image-and-entrypoint.md) |
| 18 | [18: Custom user model](./phase-2-project-foundation/18-custom-user-model.md) |
| 19 | [19: Database integration with read-replica routing](./phase-3-infrastructure-integration/19-database-and-replica-routing.md) |
| 20 | [20: Cache integration](./phase-3-infrastructure-integration/20-cache-integration.md) |
| 21 | [21: Channel layer integration](./phase-3-infrastructure-integration/21-channel-layer-integration.md) |
| 22 | [22: Task queue integration](./phase-3-infrastructure-integration/22-task-queue-integration.md) |
| 23 | [23: Object storage integration](./phase-3-infrastructure-integration/23-object-storage-integration.md) |
| 24 | [24: Email backend integration](./phase-3-infrastructure-integration/24-email-backend-integration.md) |
| 25 | [25: Application metrics and log shipping](./phase-3-infrastructure-integration/25-metrics-and-log-shipping.md) |
| 26 | [26: Health endpoint and edge routing](./phase-3-infrastructure-integration/26-health-endpoint-and-edge-routing.md) |
| 27 | [27: API foundation and error envelope](./phase-4-rest-api/27-api-foundation-and-error-envelope.md) |
| 28 | [28: Framework and middleware status-code contract](./phase-4-rest-api/28-framework-and-middleware-status-codes.md) |
| 29 | [29: Token authentication endpoints](./phase-4-rest-api/29-token-authentication-endpoints.md) |
| 30 | [30: JSON web token endpoints](./phase-4-rest-api/30-json-web-token-endpoints.md) |
| 31 | [31: User registration and profile endpoints](./phase-4-rest-api/31-user-registration-and-profile.md) |
| 32 | [32: Account activation and resend](./phase-4-rest-api/32-account-activation-and-resend.md) |
| 33 | [33: Password management endpoints](./phase-4-rest-api/33-password-management-endpoints.md) |
| 34 | [34: Username management endpoints](./phase-4-rest-api/34-username-management-endpoints.md) |
| 35 | [35: Throttling, permissions, and security headers](./phase-4-rest-api/35-throttling-permissions-security-headers.md) |
| 36 | [36: Exhaustive OpenAPI documentation](./phase-4-rest-api/36-exhaustive-openapi-documentation.md) |
| 37 | [37: Offline API documentation UIs](./phase-4-rest-api/37-offline-api-documentation-uis.md) |
| 38 | [38: ASGI protocol routing and consumer base](./phase-5-websockets/38-asgi-routing-and-consumer-base.md) |
| 39 | [39: WebSocket authentication](./phase-5-websockets/39-websocket-authentication.md) |
| 40 | [40: User notification channel with group broadcast](./phase-5-websockets/40-notification-channel-group-broadcast.md) |
| 41 | [41: WebSocket error and close-code contract](./phase-5-websockets/41-websocket-error-and-close-codes.md) |
| 42 | [42: Background worker service](./phase-6-async-services/42-background-worker-service.md) |
| 43 | [43: Periodic task scheduler](./phase-6-async-services/43-periodic-task-scheduler.md) |
| 44 | [44: Worker monitoring dashboard](./phase-6-async-services/44-worker-monitoring-dashboard.md) |
| 45 | [45: Asynchronous transactional email](./phase-6-async-services/45-asynchronous-transactional-email.md) |
| 46 | [46: Background-to-WebSocket event fan-out](./phase-6-async-services/46-background-to-websocket-fanout.md) |
| 47 | [47: Unit test suite](./phase-7-testing-and-audit/47-unit-test-suite.md) |
| 48 | [48: Integration test suite](./phase-7-testing-and-audit/48-integration-test-suite.md) |
| 49 | [49: Dual-mode test execution](./phase-7-testing-and-audit/49-dual-mode-test-execution.md) |
| 50 | [50: Convention audit](./phase-7-testing-and-audit/50-convention-audit.md) |
| 51 | [51: Security and reliability audit](./phase-7-testing-and-audit/51-security-and-reliability-audit.md) |
| 52 | [52: Final verification and handover](./phase-7-testing-and-audit/52-final-verification-and-handover.md) |
| 53 | [53: SOLID audit baseline and module inventory](./phase-8-solid-architecture/53-solid-audit-baseline-and-module-inventory.md) |
| 54 | [54: Operator orchestration SOLID audit](./phase-8-solid-architecture/54-operator-orchestration-solid-audit.md) |
| 55 | [55: Support script SOLID audit](./phase-8-solid-architecture/55-support-script-solid-audit.md) |
| 56 | [56: Django runtime SOLID audit](./phase-8-solid-architecture/56-django-runtime-solid-audit.md) |
| 57 | [57: Account authentication SOLID audit](./phase-8-solid-architecture/57-account-authentication-solid-audit.md) |
| 58 | [58: Account lifecycle SOLID audit](./phase-8-solid-architecture/58-account-lifecycle-solid-audit.md) |
| 59 | [59: Notification delivery SOLID audit](./phase-8-solid-architecture/59-notification-delivery-solid-audit.md) |
| 60 | [60: Test architecture SOLID audit](./phase-8-solid-architecture/60-test-architecture-solid-audit.md) |
| 61 | [61: Cross-module dependency audit](./phase-8-solid-architecture/61-cross-module-dependency-audit.md) |
| 62 | [62: Complete SOLID verification](./phase-8-solid-architecture/62-complete-solid-verification.md) |
| 63 | [63: Phase 8 handover](./phase-8-solid-architecture/63-phase-8-handover.md) |
| 64 | [64: Public deployment scope and configuration contract](./phase-9-public-edge-email/64-public-deployment-scope.md) |
| 65 | [65: Cloudflare DNS and email identity](./phase-9-public-edge-email/65-cloudflare-dns-and-email-identity.md) |
| 66 | [66: Cloudflare Tunnel edge](./phase-9-public-edge-email/66-cloudflared-public-edge.md) |
| 67 | [67: Resend development transport](./phase-9-public-edge-email/67-resend-development-transport.md) |
| 68 | [68: Public runtime security hardening](./phase-9-public-edge-email/68-public-runtime-security.md) |
| 69 | [69: Public runtime truth verification](./phase-9-public-edge-email/69-public-runtime-verification.md) |
| 70 | [70: Phase 9 handover](./phase-9-public-edge-email/70-phase-9-handover.md) |

## Dependency graph

Arrows point from blocker to blocked. Tickets on the same line can run in parallel.

```text
01 ──> 02 ──> 03 ──┬──> 04 ──> 05
                   │     └──> 12
                   ├──> 06
                   ├──> 07
                   ├──> 08
                   ├──> 09
                   └──> 10
04,06,07 ────────────> 11

01 ──> 13 ──┬──> 14 ──┬──> 17
            ├──> 15   └──> 18
            └──> 16

04,17,18 ──> 19 ─┐
06,17 ─────> 20 ─┤
06,17 ─────> 21 ─┤
07,17 ─────> 22 ─┼──> 26 ──> 27 ──> 28
08,17 ─────> 23 ─┤              └──> 29,30 ──> 31 ──┬──> 32
09,17 ─────> 24 ─┤                                  ├──> 33
11,17 ─────> 25 ─┘                                  ├──> 34
10 ────────────────────────────────────────────────>└──> 35

28,31,32,33,34,35 ──> 36 ──> 37

21,27 ──> 38 ──> 39 ──> 40 ──> 41
              (30)

22 ──> 42 ──┬──> 43
            ├──> 44
            ├──> 45  (also 32,33,34)
            └──> 46  (also 40)

16 + phases 4-6 ──> 47 ──> 48 ──> 49 ──┬──> 50 ──┐
                                       └──> 51 ──┴──> 52

52 ──> 53 ──┬──> 54 ────────────────┐
            ├──> 55 ────────────────┤
            ├──> 56 ──> 59 ─────────┤
            └──> 57 ──> 58 ─────────┼──> 60 ──> 61 ──> 62 ──> 63

63 ──> 64 ──┬──> 65 ──┬──> 67 ──┐
            │         └──> 66 ──┤
            └──────────────> 66 ─┤
                     66,67 ──> 68 ──> 69 ──> 70
```

## Standards every ticket inherits

Stated once here so no ticket restates them.

**Code contains no comments.** Not sparse comments — none. Every explanation lives in a docstring.

**Docstrings are structured**, in every module the project owns, tests included:

| Level | Must contain |
| --- | --- |
| File | One-line title, then a 2–3 line description |
| Class | One-line title, 2–3 line description, what it inherits, its attributes and members |
| Function / method | One-line title, 2–3 line description, arguments, returns, raises |

**Tests run in parallel.** Every suite is safe under `pytest -n auto`. A test that needs serial execution must say so
with a marker and justify it in its docstring. No suite may hang: every network-touching test carries a timeout.

**Both environments, always.** A ticket that changes runtime behaviour updates `development` and `testing` together.

**Nothing is hardcoded.** Configuration comes from the environment variable inventory in
[docs/platform/conventions.md](../docs/platform/conventions.md).

**Names come from the registry.** Never invent a container, volume, or network name.

**Runtime truth, not test-shaped confidence.** After the complete test gate, run the affected stack, exercise the
changed seam, inspect project-scoped health and ownership, and review a bounded log window. A healthy endpoint that
emits a failure-level terminal record is a defect. Record expected warnings in their governing source; never
silently accept them because the response or tests succeeded.

## Definition of done for the whole set

- Every development service healthy, named to the registry, and audited.
- Every documented route returns every documented status code, and the schema proves it.
- WebSockets authenticate and broadcast across two processes.
- The suite passes in a container and on the host, at 100% branch coverage, in parallel.
- Every project-owned Python module has a Phase 8 SOLID disposition backed by source and test evidence.
- Every confirmed SOLID violation is fixed without changing the bounded application surface.
- `uv run poe check` is green.
