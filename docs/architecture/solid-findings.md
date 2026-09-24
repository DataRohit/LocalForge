# Phase 8 SOLID findings

Status: Ticket 53 baseline complete. Area audits remain pending.

This ledger records evidence and dispositions under
[the Phase 8 SOLID audit plan](./solid-audit-plan.md). File size, class count, naming preference, and hypothetical
future variation are not findings.

## Baseline

The baseline revision is `b79c18913f2d581bbdc6baca810223613598840b`. That commit changes documentation only
after the completed Phase 7 source and runtime verification, so the executable tree and built image inputs match
the Phase 7 handover evidence.

The operator reran this complete sequence on 2026-09-24 before Phase 8:

```console
uv sync --all-groups --frozen
uv run poe docker-clean-check
uv run poe help
uv run poe environments-setup --proxy-only
uv run poe environments-setup --proxy-only
uv run poe development-health
uv run poe testing-health
uv run poe docker-audit
uv run poe testing-test-both
uv run poe testing-integration-audit
uv run poe check
uv run poe convention-audit
uv run poe security-audit
uv run poe development-health
uv run poe testing-health
uv run poe docker-audit
```

All commands passed. Pytest reported no warnings or skips. The security audit kept the four required Django
deployment warnings (`security.W004`, `security.W008`, `security.W012`, and `security.W016`) visible and
machine-enforced. The durable Phase 7 evidence records:

- 2,112 tests collected in each mode;
- 2,089 core and 23 security-timing tests passed in each mode;
- 100% branch coverage;
- zero pytest warnings and zero skips;
- clean Django, OpenAPI, Ruff, formatting, structured-docstring, mypy, and ty checks;
- passing convention and security audits;
- passing integration lifecycle, persistence, degraded-readiness, and recovery exercises;
- exact temporary Mailpit ownership and teardown;
- 22 healthy or intentionally healthless development containers and six healthy testing containers;
- 28 project containers with exact network, volume, image, service, and ownership labels;
- zero warning-or-higher runtime records in the final bounded observation window.

Ticket 53 reran `development-health`, `testing-health`, and `docker-audit` at the baseline revision. All passed with
the same 22 development containers, six testing containers, eight networks, and 21 volumes. No production source,
runtime configuration, dependency, image input, route, protocol, command, or service changed before this baseline.

## Inventory method

An AST inventory enumerated every project-owned Python file under `src/`, `scripts/`, `tests/`, and
`.github/scripts/`. Historical migrations and installed `.agents/` skill code are excluded by the governing scope.
The inventory contains 182 modules. Deterministic cluster rules assign every module once: 64 production and
operator modules, plus 118 test modules. The runtime import scan excludes imports guarded by `TYPE_CHECKING` and
found no project-owned runtime import cycle. `accounts.models` and `accounts.managers` retain one type-only cycle.

Each row below owns its listed modules and evaluates all five principles. `Review` means the area ticket must inspect
the named evidence before declaring the cluster clear or recording a finding. `N/A` includes the reason the
principle does not apply.

## Area ownership

| Ticket | Area | Clusters | Modules | Status |
| --- | --- | --- | ---: | --- |
| 54 | Operator orchestration | `54-OP1` | 1 | Complete: `F54-001` fixed |
| 55 | Support scripts | `55-S1` to `55-S7` | 12 | Complete: `F55-001` fixed |
| 56 | Django runtime | `56-R1` to `56-R4` | 23 | Baseline mapped |
| 57 | Account authentication | `57-A1` to `57-A3` | 13 | Baseline mapped |
| 58 | Account lifecycle | `58-L1` to `58-L4` | 9 | Baseline mapped |
| 59 | Notification delivery | `59-N1` to `59-N2` | 6 | Baseline mapped |
| 60 | Test architecture | `60-T1` to `60-T7` | 118 | Baseline mapped |
| 61 | Cross-module dependencies | all clusters | 182 | Baseline map has no cycle |

## Module inventory

| Cluster | Modules | Callers and interface | Composition root and adapters | SRP | OCP | LSP | ISP | DIP |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `54-OP1` | `scripts/manage_platform.py` (1) | Poe and operators call stable command names, arguments, output, and exit codes | `main` selects `HostRunner`, Compose, clock, HTTP, filesystem, and process adapters | Clear: setup, image, lifecycle, health, ownership, testing, log, and cleanup functions retain cohesive policies behind one operator interface | Clear: the fixed command surface reuses environment specs, target registries, and shared lifecycle policy without a plugin system | Clear: `HostRunner`, fake runners, probes, clocks, and result records preserve their structural contracts | Fixed `F54-001`: lifecycle callers no longer receive unused sleep or clock dependencies | Clear: `main` selects concretes while policy accepts runners, probes, sleep, and clocks only where behavior varies |
| `55-S1` | `scripts/__init__.py` (1) | Package marker imported by script and test modules | No composition | Clear package identity | N/A: no extension policy | N/A: no substitute | Clear empty interface | N/A: no policy |
| `55-S2` | `scripts/audit_naming.py`, `scripts/audit_security.py` (2) | Poe audit commands and unit tests call parser, `main`, and audit functions | Each `main` selects a runner and concrete Docker, HTTP, scanner, and Git adapters | Clear: convention and security policies have separate owners | Clear: fixed audit scopes reuse data-driven inventory and checks | Clear: host and fake runners preserve captured result contracts | Clear: one command method plus narrow result records | Clear: external tools sit behind the runner seam |
| `55-S3` | `scripts/gen_secrets.py`, `scripts/sops_env.py` (2) | Setup commands call manifest generation and encryption interfaces | Git index, filesystem, randomness, and SOPS process adapters | Clear: generation/refusal and encryption/atomic-write lifecycles are separate | Clear: `SECRET_RECIPES`, environment groups, and composed values accept registered variation | Clear: version-control and environment adapters preserve failures and outputs | Clear: prepared state contains only one environment write transaction | Fixed `F55-001`: generated persistence policy now supplies the SeaweedFS KEK seam |
| `55-S4` | `scripts/preflight.py`, `scripts/wait_for_services.py` (2) | Operators and entrypoints call report/readiness commands | Probe protocols select host tools and service clients | Clear: prerequisite grading and runtime readiness remain separate modules | Clear: only the fixed service registry dispatches existing probe variation | Clear: real and fake probes preserve success-by-return and failure-by-exception | Clear: each caller receives the cohesive probe surface it uses | Clear: host and network clients remain behind probe seams |
| `55-S5` | `scripts/prepare_broker.py`, `scripts/seed_storage.py`, `scripts/celery_worker_health.py` (3) | Container entrypoints and health checks call one supported command each | Broker, S3, Celery, and transient reply adapters | Clear: each command owns one service lifecycle | N/A: fixed service commands expose no extension policy | Clear: bucket, Celery, broker, and reply adapters preserve cleanup and errors | Clear: protocols expose only called client operations | Clear: concrete clients are selected at command composition roots |
| `55-S6` | `scripts/check_docstrings.py` (1) | Poe documentation gate calls `main`; unit tests call parser/check functions | AST and filesystem at command boundary | Clear: parsing, rule evaluation, and reporting serve one documentation contract | Clear: rule functions extend without changing file discovery or reporting | N/A: no substitutable runtime adapter beyond file inputs | Clear: records expose only parsed documentation evidence | Clear: pure policy is separate from command-boundary filesystem discovery |
| `55-S7` | `.github/scripts/validate_commit_message.py` (1) | Commit-msg hook calls `main`; policy functions accept message text | Filesystem is confined to command boundary | Clear: one commit-message policy | Clear: validation rules remain independent checks | N/A: no substitute | Clear: text-in, diagnostics-out interface | Clear: pure policy sits below the command boundary |
| `56-R1` | `src/manage.py`, `src/config/__init__.py`, `src/config/settings/*.py` (6) | Django, Celery, Uvicorn, tests, and management commands import settings and application roots | Environment modules select all concrete framework adapters | Review settings loading versus policy validation | Review two-environment variation through existing modules only | Django/Celery hooks must preserve framework contracts | Review whether consumers depend on unrelated settings | Framework composition roots may import concrete adapters |
| `56-R2` | `src/config/api.py`, `src/config/api_errors.py`, `src/config/security.py` (3) | ASGI/Django middleware, DRF, schema generation, views, and tests use boundary policy | `config.asgi` and Django middleware compose admission, errors, CORS, and security headers | Review admission, correlation, schema, error, and browser-security ownership | Review repeated response-policy dispatch | Middleware and exception hooks must preserve framework events and responses | Review request/scope context breadth | Review cache, executor, and framework details behind existing seams |
| `56-R3` | `src/config/asgi.py`, `wsgi.py`, `urls.py`, `routing.py`, `metrics_asgi.py`, `metrics_urls.py` (6) | Servers import ASGI/WSGI roots; Django resolves HTTP and WebSocket routes | Explicit composition roots select middleware, routes, consumers, and metrics | Clear composition responsibility, subject to area review | Route extension is governed and fixed; no speculative registry | ASGI/WSGI callables must preserve protocol contracts | Clear narrow exported application and route interfaces | Concrete imports are allowed at composition roots |
| `56-R4` | `src/config/cache.py`, `celery.py`, `channels.py`, `db_router.py`, `email.py`, `health.py`, `logs.py`, `tasks.py` (8) | Settings, views, workers, health, consumers, scripts, and tests call runtime adapters | Settings and Celery roots select cache, channel, DB, mail, broker, logging, and task adapters | Review each runtime policy and lifecycle boundary | Review existing host/container and backend variation only | Backends, routers, layers, tasks, formatters, and probes require compatible contracts | Review broad readiness and logging helper surfaces | Review concrete transport/store access behind test and runtime seams |
| `57-A1` | `src/accounts/__init__.py`, `admin.py`, `apps.py`, `managers.py`, `models.py`, `normalisation.py` (6) | Django ORM/admin, authentication, lifecycle services, notifications, tasks, and tests use identity persistence | Django app registry and ORM compose model, manager, admin, and constraints | Review identity versus credential-record persistence cohesion | Review model addition effects without repository abstraction | Manager, model, and admin hooks preserve Django contracts | Review callers that need only identity or one credential record | ORM concretes are allowed at persistence composition boundary |
| `57-A2` | `src/accounts/authentication.py`, `jwt_authentication.py`, `token_authentication.py`, `urls.py` (4) | DRF authentication, fixed routes, account workflows, notifications, and tests use credential operations | DRF settings select token/JWT adapters; the account URL composition root selects authentication and all lifecycle views | Review authentication policy versus HTTP transport and token lifecycle | Review duplicated token-operation dispatch | DRF/SimpleJWT subclasses and auth adapters preserve failure contracts | Review broad account/request/settings dependencies | Review clocks, token stores, hashes, and ORM behind observed seams |
| `57-A3` | `src/accounts/api_throttling.py`, `login_throttle.py`, `request_throttling.py` (3) | HTTP and WebSocket admission callers use address, account, and cache/DB decisions | Views and middleware select cache or PostgreSQL admission stores | Review identity parsing, policy, and persistence ownership | Review repeated dimension dispatch | Cache and PostgreSQL decisions must share admission semantics where substituted | Review request and identity inputs for unrelated fields | Existing cache/DB and test seams should carry concrete storage |
| `58-L1` | `src/accounts/account_activation.py`, `activation_tokens.py` (2) | Registration, admin, tasks, views, and tests call activation issuance/confirmation | Views and tasks compose ORM, signed token, throttle, email, and transaction adapters | Review issuance, admission, confirmation, and delivery boundaries | Review shared lifecycle policy before extraction | Token and throttle helpers must preserve classification and timing | Review account/request context passed through activation layers | Review clock, token, ORM, task, and mail seams |
| `58-L2` | `src/accounts/password_management.py`, `password_tokens.py` (2) | Password views, tasks, account services, and tests call change/reset interfaces | Views compose serializers, ORM, tokens, throttles, tasks, auth revocation, and validators | Review change versus recovery lifecycle ownership | Review duplication with other recovery workflows using evidence only | Token and recovery adapters preserve classification and postconditions | Review broad account/request dependencies | Review clock, transaction, token, task, and mail seams |
| `58-L3` | `src/accounts/username_management.py`, `username_tokens.py` (2) | Username views, tasks, profile services, and tests call change/reset interfaces | Views compose serializers, ORM, tokens, throttles, tasks, and validators | Review change versus recovery lifecycle ownership | Review proven recovery duplication without generic workflow factory | Token and recovery adapters preserve classification and postconditions | Review broad account/request dependencies | Review clock, transaction, token, task, and mail seams |
| `58-L4` | `src/accounts/registration_timing.py`, `tasks.py`, `user_profiles.py` (3) | Registration/profile views and lifecycle services call timing and delivery interfaces | Views and Celery compose ORM, clock/sleep, email, notification, and transaction adapters | Review profile, timing, and multi-email task responsibilities separately | Review shared delivery policy only where tasks already vary | Celery tasks and timing callables preserve retry and clock contracts | Review task payloads and profile serializer surfaces | Existing clock, task, email, and notification seams require review |
| `59-N1` | `src/notifications/__init__.py`, `admission.py`, `authentication.py`, `protocol.py` (4) | ASGI routing, consumers, tests, and account publishers use admission/auth/protocol contracts | `config.routing` composes admission and JWT middleware around consumer | Review admission, authentication, and protocol authority boundaries | Review outcome dispatch and middleware extension only where governed | Middleware and authentication callables preserve ASGI ordering and cleanup | Review protocol fields exposed to each layer | Review cache, identity, clock, and ORM behind existing seams |
| `59-N2` | `src/notifications/delivery.py`, `websocket.py` (2) | Account tasks publish; consumers and ASGI server execute delivery/lifecycle | Routing selects consumer; channel layer is acquired at publication boundary | Review validation, publication, connection, and failure-boundary ownership | Review event-type dispatch without speculative bus | Consumer, validator, and channel adapters preserve event and cleanup contracts | Review publisher and consumer dependency surfaces | Review channel acquisition and transport details behind interfaces |
| `60-T1` | All test package markers; `tests/conftest.py`; `tests/integration/conftest.py`; `tests/unit/conftest.py`; `factories.py`; `websocket.py`; `test_harness.py` (18) | Pytest, all test modules, subprocess collection checks, and test clients use shared fixtures and helpers | Pytest hooks and fixtures compose workers, factories, communicators, and guards | Review hook, fixture, factory, and harness ownership | Review shared policy growth without helper proliferation | Fakes/factories/communicators must preserve production-visible contracts | Review broad fixtures and global hook state | Review environment, worker, network, and process seams |
| `60-T2` | `tests/integration/config/runtime_probe.py`, `throttle_worker.py` (2) | Operator lifecycle commands spawn supported probe CLIs | Parser and `main` compose HTTP, SMTP, database, and client adapters | Review one runtime-probe lifecycle per command | Review probe-command dispatch only where real variation exists | Host/container probe modes preserve output and exit behavior | Review probe records for unrelated state | Review process, network, and environment seams |
| `60-T3` | All unit and integration account test modules (30) | Pytest clients exercise account public and direct interfaces | Fixtures compose ORM, REST, SMTP, Celery, clock, and concurrency adapters | Review tests as clients; one behavior concern per module | N/A: tests do not define production extension policy | Fakes and patches must honor production contracts | Review fixture breadth and private implementation access | Review explicit clock, storage, process, and network seams |
| `60-T4` | All unit and integration config test modules except runtime probes (41) | Pytest clients exercise runtime, HTTP, settings, service, and framework interfaces | Fixtures compose Django, ASGI, services, process, and outage adapters | Review tests as clients; one runtime concern per module | N/A: tests do not define production extension policy | Fakes and framework doubles must honor production contracts | Review large setup fixtures and broad module-import helpers | Review explicit environment, process, service, and clock seams |
| `60-T5` | All unit and integration notification test modules (11) | Pytest and communicators exercise WebSocket public and direct interfaces | Fixtures compose ASGI, JWT, channel layer, Uvicorn, and process adapters | Review tests as clients; one protocol concern per module | N/A: tests do not define production extension policy | Communicators and fakes must preserve ASGI/channel contracts | Review fixture and protocol-context breadth | Review process, clock, channel, and identity seams |
| `60-T6` | All unit script and Compose test modules (14) | Pytest exercises command interfaces, policy functions, and configuration artifacts | Fakes compose runner, filesystem, Docker, HTTP, Git, and service adapters | Review tests as clients; separate command and policy evidence | N/A: tests do not define production extension policy | Fake runners/probes must preserve production contracts | Review shared fake breadth and copied setup | Review explicit process, filesystem, Docker, and network seams |
| `60-T7` | `tests/unit/test_dependencies.py`, `tests/unit/test_manage.py` (2) | Pytest enforces dependency and management-entrypoint contracts | Tests inspect installed metadata and substitute the Django management callable | Review one repository contract per module | N/A: tests do not define production extension policy | Review fakes and patches against production callable contracts | Review only imported contract data | Review environment and package metadata seams |

## Dependency map

Static imports establish these intended directions:

1. Composition roots in `config.settings`, `config.asgi`, `config.routing`, `config.urls`, and Celery select concrete
   framework and service adapters.
2. Account lifecycle modules depend on account identity, authentication, throttle, token, task, and shared runtime
   interfaces. Authentication policy modules do not depend on lifecycle views. The account URL composition root
   intentionally imports both authentication and lifecycle views.
3. Notification authentication depends on account identity. Notification admission reuses request-address and
   throttle policy. Account email tasks publish through notification delivery, not through the consumer.
4. Runtime modules may import account or notification modules only at documented composition and maintenance
   boundaries.
5. Operator orchestration depends on secret generation. Support scripts do not import application policy except
   the worker-health probe, which intentionally loads the Celery composition root.
6. Tests depend on production modules. Production modules do not import tests.
7. No project-owned runtime import cycle was found at the baseline revision. The type-only
   `accounts.models`/`accounts.managers` cycle is excluded from runtime edges and remains explicit for Ticket 61.

Ticket 61 must rerun the graph after every area repair and decide whether stable direction rules merit machine
enforcement. The absence of a cycle does not by itself prove correct dependency inversion.

## Baseline evidence by area

| Area | Smallest existing evidence before refactoring | Complete preservation gate |
| --- | --- | --- |
| Operator orchestration | `uv run pytest tests/unit/scripts/test_platform.py --no-cov -q` | `testing-test-both`, affected health/ownership/log gates |
| Support scripts | `uv run pytest tests/unit/scripts tests/unit/compose/test_foundation.py --no-cov -q` | `check`, `convention-audit`, `security-audit` as affected |
| Django runtime | `uv run pytest tests/unit/config tests/unit/test_manage.py tests/integration/config -m "not security_timing" --no-cov -q` | Both environment health, Docker audit, deployed seam, logs |
| Account authentication | `uv run pytest tests/unit/accounts/test_admin.py tests/unit/accounts/test_apps.py tests/unit/accounts/test_authentication.py tests/unit/accounts/test_api_throttling.py tests/unit/accounts/test_jwt_authentication.py tests/unit/accounts/test_login_throttle.py tests/unit/accounts/test_managers.py tests/unit/accounts/test_models.py tests/unit/accounts/test_normalisation.py tests/unit/accounts/test_request_throttling.py tests/unit/accounts/test_token_authentication.py tests/unit/accounts/test_urls.py tests/integration/accounts/test_jwt_authentication.py tests/integration/accounts/test_models.py tests/integration/accounts/test_token_authentication.py -m "not security_timing" --no-cov -q` | Exact OpenAPI contract and complete test modes |
| Account lifecycle | `uv run pytest tests/unit/accounts/test_account_activation.py tests/unit/accounts/test_activation_tokens.py tests/unit/accounts/test_password_management.py tests/unit/accounts/test_password_tokens.py tests/unit/accounts/test_registration_timing.py tests/unit/accounts/test_tasks.py tests/unit/accounts/test_user_profiles.py tests/unit/accounts/test_username_management.py tests/unit/accounts/test_username_tokens.py tests/integration/accounts/test_account_activation.py tests/integration/accounts/test_async_email.py tests/integration/accounts/test_password_management.py tests/integration/accounts/test_registration_timing.py tests/integration/accounts/test_user_profiles.py tests/integration/accounts/test_username_management.py -m "not security_timing" --no-cov -q` | REST, SMTP, worker, timing, OpenAPI, and complete test modes |
| Notification delivery | `uv run pytest tests/unit/notifications tests/integration/notifications tests/unit/config/test_routing.py --no-cov -q` | Deployed WebSocket, multi-process delivery, and logs |
| Test architecture | `uv run pytest tests/unit/test_harness.py --no-cov -q` | Exact 2,112 collection parity and complete dual-mode pass |
| Cross-module | `uv run poe typecheck` and `uv run pytest tests/unit/test_dependencies.py tests/unit/test_harness.py --no-cov -q` | Complete quality, security, convention, runtime, and audit gates |

## Findings

No required finding or improvement is confirmed by the Ticket 53 baseline alone. Each area ticket must add a row
before editing code and close it only after interface-level regression evidence and the applicable complete gates.

| ID | Priority | Principle | Module | Evidence and impact | Proposed seam | Disposition | Verification |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `F54-001` | Improvement | ISP | `scripts/manage_platform.py` environment and testing lifecycle functions | `up`, `rebuild`, and `reset` accepted sleep and clock callables they discarded; four testing workflows forwarded the same unused state. Every caller and test had to construct irrelevant dependencies, and lint suppressions hid the breadth. | Remove unused dependencies from each callable and every forwarding layer while retaining clock and sleep seams only in timed setup, polling, and test execution. | Fixed in Ticket 54 without changing public command names, arguments, output, exit codes, Compose operations, cleanup, or resource ownership. | 101 operator tests and the complete 677-test support/Compose layer passed in parallel; Ruff, structured docstrings, mypy, and ty passed; both environments rebuilt and health/ownership passed; convention and security audits passed; the final 28-container window had zero restarts and no unexplained warning-or-higher records. |
| `F55-001` | Required | DIP | `scripts/gen_secrets.py` and SeaweedFS startup configuration | Both SeaweedFS containers logged that SSE-S3 key material would use an implicit plaintext fallback, and unused embedded IAM logged a signing-key error. High-level persistence and security policy therefore depended on concrete vendor defaults outside the generated-secret interface. | Add a distinct persistent KEK passphrase to the manifest and `SECRET_RECIPES`, bind forced rotation to each SeaweedFS data volume, require it at startup, and disable unused IAM. | Fixed in Ticket 55; encrypted environment artifacts contain independently generated values without disclosure, and exact manifest parity rejects obsolete encrypted secret names. | 677 support/Compose tests; strict types; documentation gate; both environments rebuilt and healthy; convention and security audits passed; `2026-09-24T03:56:22.0362093Z..2026-09-24T03:59:26.6154034Z` covered all 28 containers with zero restarts and no unexplained warning-or-higher records. |

## Observations

| ID | Module | Concern | Why no code change in Ticket 53 | Revisit trigger |
| --- | --- | --- | --- | --- |
| `O53-001` | `scripts/manage_platform.py` | One module coordinates many operator lifecycles | Size and function count are not violations; command contracts and shared ownership may justify the current depth | Ticket 54 finds unrelated policy change spread, broad interfaces, or blocked tests |
| `O53-002` | `src/accounts/models.py` | Identity and three credential-record types share one ORM module | All records share account persistence, cleanup, and transaction policy; moving files alone adds no leverage | Tickets 57-58 find callers forced through unrelated persistence interfaces |
| `O53-003` | `src/config/logs.py` | Logging, redaction, correlation, streaming finalization, and metrics boundaries interact | They enforce one end-to-end observability and secrecy contract; extraction without change evidence may make policy shallower | Ticket 56 finds independent change actors or invalid adapter contracts |
| `O53-004` | Account recovery modules | Activation, password, and username flows have visible structural similarity | Enumeration, token, transaction, retry, timing, and response contracts differ; a generic workflow is speculative | Ticket 58 proves repeated policy changes or a tested shared seam |
| `O53-005` | Test suite | 118 modules use shared hooks, factories, probes, and communicators | Test count and helper count are not violations; current parallel and dual-mode evidence is green | Ticket 60 finds broad fixtures, contract-breaking fakes, global state, or private-seam coupling |
| `O53-006` | `accounts.models`, `accounts.managers` | Static typing creates a type-only cycle while runtime imports remain one-way | `TYPE_CHECKING` preserves the manager's precise model type without a runtime cycle or service locator | Ticket 61 finds runtime coupling, blocked typing, or broader dependency spread |

## Ticket 53 disposition

All 182 scoped Python modules have one cluster, one area owner, a caller/interface description, composition-root or
adapter disposition, and an evaluation of SRP, OCP, LSP, ISP, and DIP. No production behavior changed. Tickets
54-61 now replace `Review` entries with evidence-backed clear, fixed, not-applicable, or observation dispositions.
Independent GPT-5.6 Terra and GPT-5.6 Sol audits reported no legitimate finding after two remediation rounds.
