# Phase 8 SOLID findings

Status: Phase 8 complete. All eleven tickets, 53-63, are closed.

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
The Ticket 53 baseline contains 182 modules. Tickets 56-58 add `src/config/openapi.py`,
`src/accounts/credentials.py`, `src/accounts/request_validation.py`, `tests/unit/accounts/test_credentials.py`, and
`tests/unit/accounts/test_request_validation.py`; they also replace registration-specific timing names with
`accounts.response_timing` and its matching unit test, and add the mirrored `tests/unit/config/test_openapi.py`.
Ticket 61 adds `tests/unit/test_architecture.py`. The current inventory has 189 modules: 67 production and operator
modules plus 122 test modules. Deterministic cluster rules assign every module once. The runtime import scan
excludes imports guarded by `TYPE_CHECKING` and found no project-owned runtime import cycle.
`accounts.models` and `accounts.managers` retain one type-only cycle.

Each row below owns its listed modules and evaluates all five principles. `Review` means the area ticket must inspect
the named evidence before declaring the cluster clear or recording a finding. `N/A` includes the reason the
principle does not apply.

## Area ownership

| Ticket | Area | Clusters | Modules | Status |
| --- | --- | --- | ---: | --- |
| 54 | Operator orchestration | `54-OP1` | 1 | Complete: `F54-001` fixed |
| 55 | Support scripts | `55-S1` to `55-S7` | 12 | Complete: `F55-001` fixed |
| 56 | Django runtime | `56-R1` to `56-R4` | 24 | Complete: `F56-001` fixed |
| 57 | Account authentication | `57-A1` to `57-A3` | 14 | Complete: `F57-001` fixed |
| 58 | Account lifecycle | `58-L1` to `58-L4` | 10 | Complete: `F58-001` and `F58-002` fixed |
| 59 | Notification delivery | `59-N1` to `59-N2` | 6 | Complete: clear with two observations |
| 60 | Test architecture | `60-T1` to `60-T7` | 122 | Complete: clear with two observations |
| 61 | Cross-module dependencies | all clusters | 189 | Complete: `F61-001` fixed; no runtime cycle |

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
| `56-R1` | `src/manage.py`, `src/config/__init__.py`, `src/config/settings/*.py` (6) | Django, Celery, Uvicorn, tests, and management commands import settings and application roots | Environment modules select all concrete framework adapters | Clear: loading, shared policy, and two environment compositions remain distinct | Clear: only the two governed environment modules vary | Clear: Django and Celery entry hooks preserve framework contracts | Clear: callers import the environment or application object they use | Clear: concrete adapters belong in these composition roots |
| `56-R2` | `src/config/api.py`, `api_errors.py`, `openapi.py`, `security.py` (4) | ASGI/Django middleware, DRF, schema generation, views, and tests use HTTP contract policy | `config.asgi`, Django middleware, and schema settings compose runtime and documentation interfaces separately | Fixed `F56-001`: runtime and OpenAPI responsibilities now have separate modules | Clear: registry and hook tables accept governed status and schema variation | Clear: middleware, handlers, exception hooks, and schema hooks preserve framework contracts | Clear: runtime callers no longer import OpenAPI-only evidence and builders | Clear: cache/executor/framework concretes remain behind runtime seams; schema policy is pure |
| `56-R3` | `src/config/asgi.py`, `wsgi.py`, `urls.py`, `routing.py`, `metrics_asgi.py`, `metrics_urls.py` (6) | Servers import ASGI/WSGI roots; Django resolves HTTP and WebSocket routes | Explicit composition roots select middleware, routes, consumers, and metrics | Clear: each root owns one transport or route composition | N/A: route extension is fixed by governing scope | Clear: ASGI/WSGI callables preserve protocol events and errors | Clear: exported applications and route tables are narrow | Clear: concrete imports are allowed at composition roots |
| `56-R4` | `src/config/cache.py`, `celery.py`, `channels.py`, `db_router.py`, `email.py`, `health.py`, `logs.py`, `tasks.py` (8) | Settings, views, workers, health, consumers, scripts, and tests call runtime adapters | Settings and Celery roots select cache, channel, DB, mail, broker, logging, and task adapters | Clear: each module owns one runtime policy or cohesive end-to-end lifecycle | Clear: existing backend, environment, and task variation uses framework registries or explicit tables | Clear: backends, routers, layers, tasks, formatters, and probes preserve accepted domains, cleanup, and errors | Clear: cache, channel, router, email, readiness, logging, and task interfaces remain cohesive | Clear: framework composition selects concretes; real test seams cover process, clock, storage, and transport variation |
| `57-A1` | `src/accounts/__init__.py`, `admin.py`, `apps.py`, `managers.py`, `models.py`, `normalisation.py` (6) | Django ORM/admin, authentication, lifecycle services, notifications, tasks, and tests use identity persistence | Django app registry and ORM compose model, manager, admin, and constraints | Clear: identity persistence, credential records, manager creation, normalization, and admin hooks share authoritative account state | Clear: governed model additions use Django's registry and constraints without a repository abstraction | Clear: manager, model, and admin hooks preserve Django contracts | Clear: callers import only the model, manager, normalization, or admin interface they use | Clear: ORM concretes remain at the persistence composition boundary |
| `57-A2` | `src/accounts/authentication.py`, `credentials.py`, `jwt_authentication.py`, `token_authentication.py`, `urls.py` (5) | DRF authentication, fixed routes, account workflows, notifications, and tests use credential operations | DRF settings select token/JWT adapters; the account URL composition root selects authentication and all lifecycle views | Fixed `F57-001`: credential policy is separate from token and JWT transports | Clear: token/JWT variation uses explicit adapters and fixed route composition, not a plugin system | Clear: DRF/SimpleJWT subclasses, authenticators, and credential snapshots preserve failure and locked-state contracts | Clear: lifecycle callers depend on the credential functions they use, not token HTTP transport | Clear: password hashing, ORM, token stores, and framework details sit behind credential or transport modules |
| `57-A3` | `src/accounts/api_throttling.py`, `login_throttle.py`, `request_throttling.py` (3) | HTTP and WebSocket admission callers use address, account, and cache/DB decisions | Views and middleware select cache or PostgreSQL admission stores | Clear: address parsing, cache admission, and authoritative login admission have separate owners | Clear: real throttle dimensions use explicit rule collections and scope subclasses | Clear: cache and PostgreSQL decisions preserve admitted/retry semantics | Clear: request parsing and store interfaces expose only required identity and rule data | Clear: cache and PostgreSQL access remain behind their existing decision seams |
| `58-L1` | `src/accounts/account_activation.py`, `activation_tokens.py` (2) | Registration, admin, tasks, views, and tests call activation issuance/confirmation | Views and tasks compose ORM, signed token, throttle, email, and transaction adapters | Clear: issuance, delivery claim, confirmation, and consumption form one activation lifecycle | Clear: supported activation variation uses token and admission policy without a workflow factory | Clear: signed token and throttle helpers preserve classifications and timing | Clear: serializers, account identifiers, and claims expose only activation data | Clear: clock, ORM, task, throttle, and mail details remain behind lifecycle seams |
| `58-L2` | `src/accounts/password_management.py`, `password_tokens.py` (2) | Password views, tasks, account services, and tests call change/reset interfaces | Views compose serializers, ORM, tokens, throttles, tasks, auth revocation, and validators | Clear: authenticated change and recovery share credential replacement and revocation policy | Clear: common request/timing policy is extracted; password-specific token/transaction rules remain local | Clear: reset tokens and credential policy preserve classification and postconditions | Clear: callers depend on strict requests, normalized email, credential policy, or password workflow as needed | Clear: clock, transaction, token, task, mail, and ORM details sit behind explicit seams |
| `58-L3` | `src/accounts/username_management.py`, `username_tokens.py` (2) | Username views, tasks, profile services, and tests call change/reset interfaces | Views compose serializers, ORM, tokens, throttles, tasks, and validators | Clear: authenticated change and recovery share username replacement and notification policy | Clear: common request/timing policy is extracted; username availability and token rules remain local | Clear: reset tokens and username adapters preserve classifications and credentials | Clear: callers depend on strict requests, normalized email, credential policy, or username workflow as needed | Clear: clock, transaction, token, task, mail, and ORM details sit behind explicit seams |
| `58-L4` | `src/accounts/request_validation.py`, `response_timing.py`, `tasks.py`, `user_profiles.py` (4) | Authentication and lifecycle serializers use request policy; public workflows use timing; views and tasks use profile/delivery policy | Views and Celery compose ORM, clock/sleep, email, notification, and transaction adapters | Fixed: shared request and response-timing policy no longer belongs to profile or registration transport | Clear: shared policy has real multi-workflow callers; delivery tasks retain distinct retry contracts | Clear: Celery tasks, serializer bases, and timing callables preserve framework and clock contracts | Fixed `F58-001` and `F58-002`: callers receive workflow-neutral interfaces | Clear: clock, task, email, notification, and persistence details remain behind explicit seams |
| `59-N1` | `src/notifications/__init__.py`, `admission.py`, `authentication.py`, `protocol.py` (4) | ASGI routing, consumers, and tests use admission, authentication, and protocol contracts | `config.routing` composes admission and JWT middleware around consumer | Clear: connection admission, credential classification, and outcome authority have separate modules | Clear: outcome tables and middleware composition cover governed variation without a registry | Clear: ASGI middleware and authentication adapters preserve event ordering, cancellation, close outcomes, and database cleanup | Clear: admission decisions, authentication outcomes, and protocol frames expose only their required state | Clear: cache, identity, clock, and ORM access remain behind admission/authentication seams |
| `59-N2` | `src/notifications/delivery.py`, `websocket.py` (2) | Account tasks publish; consumers and ASGI server execute delivery/lifecycle | Routing selects validators, failure boundary, and consumer; publisher acquires the configured channel layer at its adapter boundary | Clear: outbound validation/publication and connection transport lifecycle have distinct owners | Clear: fixed event dispatch and consumer handlers need no speculative bus or plugin system | Clear: consumer, validators, failure boundary, and channel events preserve ASGI/channel contracts and cleanup | Clear: publishers accept recipient/event/data; consumers receive authenticated scope and channel events | Clear: concrete channel-layer acquisition belongs at the sole publisher adapter boundary |
| `60-T1` | All test package markers; `tests/conftest.py`; `tests/integration/conftest.py`; `tests/unit/conftest.py`; `factories.py`; `websocket.py`; `test_harness.py` (18) | Pytest, all test modules, subprocess collection checks, and test clients use shared fixtures and helpers | Pytest hooks and fixtures compose workers, factories, communicators, and guards | Clear: skip policy, grouping, worker identity, network guards, factories, communicator, and harness checks have explicit owners | Clear: shared policy grows through markers, registries, and focused helpers rather than copied hooks | Clear: factories and communicator preserve production-visible account and ASGI contracts | Clear: fixtures expose worker identity, service state, or one helper concern without a broad context object | Clear: environment, worker, network, and process variation is explicit at fixtures or subprocess seams |
| `60-T2` | `tests/integration/config/runtime_probe.py`, `throttle_worker.py` (2) | Operator lifecycle commands spawn supported probe CLIs | Parser and `main` compose HTTP, SMTP, database, and client adapters | Clear: runtime lifecycle actions and spawned throttle measurements have separate modules | Clear: fixed action and case dispatch covers real host/container or admission variation | Clear: host/container probes preserve output, errors, and exit behavior | Clear: probe records carry only action-specific state | Clear: process, network, database, and environment access sit at command seams |
| `60-T3` | All unit and integration account test modules (32) | Pytest clients exercise account public and direct interfaces | Fixtures compose ORM, REST, SMTP, Celery, clock, and concurrency adapters | Clear: modules mirror account policy or one public workflow | N/A: tests consume, not define, production extension policy | Clear: fakes, clocks, stores, and patches preserve caller-visible contracts | Clear: direct policy tests use focused values; integration clients cross public workflow interfaces | Clear: clock, storage, process, and network variation uses explicit fixtures or supported adapters |
| `60-T4` | All unit and integration config test modules except runtime probes (42) | Pytest clients exercise runtime, HTTP, settings, service, and framework interfaces | Fixtures compose Django, ASGI, services, process, and outage adapters | Clear: mirrored unit modules and integration modules each own one runtime concern | N/A: tests consume, not define, production extension policy | Clear: framework doubles and fake services preserve ASGI, Django, cache, channel, and task contracts | Clear: setup remains local unless reuse or policy justifies a helper | Clear: environment, process, service, outage, and clock variation is explicit |
| `60-T5` | All unit and integration notification test modules (11) | Pytest and communicators exercise WebSocket public and direct interfaces | Fixtures compose ASGI, JWT, channel layer, Uvicorn, and process adapters | Clear: admission, authentication, protocol, delivery, task fan-out, and transport tests mirror production owners | N/A: tests consume, not define, production extension policy | Clear: communicators and fakes preserve ASGI/channel events, ordering, cancellation, and close behavior | Clear: fixtures expose only identity, communicator, or transport state required by each test | Clear: process, clock, channel, and identity variation is explicit |
| `60-T6` | All unit script and Compose test modules (14) | Pytest exercises command interfaces, policy functions, and configuration artifacts | Fakes compose runner, filesystem, Docker, HTTP, Git, and service adapters | Clear: each module mirrors one command or configuration authority | N/A: tests consume, not define, production extension policy | Clear: fake runners, probes, version control, and clients preserve production contracts | Clear: shared fakes stay inside the module whose interface they model | Clear: process, filesystem, Docker, Git, and network variation uses explicit adapters |
| `60-T7` | `tests/unit/test_architecture.py`, `test_dependencies.py`, `test_manage.py` (3) | Pytest enforces architecture, dependency, and management-entrypoint contracts | Tests parse runtime imports, inspect installed metadata, and substitute the Django management callable | Clear: each module owns one repository contract | Clear: stable import rules extend through explicit checks, not source-size or SOLID scores | Clear: management callable substitution preserves arguments and failure behavior | Clear: tests import only source graph, dependency metadata, or the entrypoint callable | Clear: environment, source, and package metadata are explicit test inputs |

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
7. Ticket 61 reran the complete 67-module production/operator graph and found no runtime import cycle. The
   type-only `accounts.models`/`accounts.managers` cycle remains excluded from runtime edges.

Ticket 61 adds `uv run poe architecture-audit` to `uv run poe check`. Its AST rules reject runtime imports of
`tests`, cross-module imports of project-private symbols, and executable project import cycles. Failure-focused
tests prove each diagnostic names the exact path, line, edge, or complete cycle membership. The rules exclude
historical migrations and `TYPE_CHECKING` edges under the same scope as this ledger.

## Baseline evidence by area

| Area | Smallest existing evidence before refactoring | Complete preservation gate |
| --- | --- | --- |
| Operator orchestration | `uv run pytest tests/unit/scripts/test_platform.py --no-cov -q` | `testing-test-both`, affected health/ownership/log gates |
| Support scripts | `uv run pytest tests/unit/scripts tests/unit/compose/test_foundation.py --no-cov -q` | `check`, `convention-audit`, `security-audit` as affected |
| Django runtime | `uv run pytest -n auto --dist loadgroup tests/unit/config tests/unit/test_manage.py -m "not security_timing" --no-cov --max-worker-restart=0 -q` and `uv run poe test-integration` | Both environment health, Docker audit, deployed seam, logs |
| Account authentication | `uv run pytest tests/unit/accounts/test_admin.py tests/unit/accounts/test_apps.py tests/unit/accounts/test_authentication.py tests/unit/accounts/test_api_throttling.py tests/unit/accounts/test_credentials.py tests/unit/accounts/test_jwt_authentication.py tests/unit/accounts/test_login_throttle.py tests/unit/accounts/test_managers.py tests/unit/accounts/test_models.py tests/unit/accounts/test_normalisation.py tests/unit/accounts/test_request_throttling.py tests/unit/accounts/test_token_authentication.py tests/unit/accounts/test_urls.py tests/integration/accounts/test_jwt_authentication.py tests/integration/accounts/test_models.py tests/integration/accounts/test_token_authentication.py -m "not security_timing" --no-cov -q` | Exact OpenAPI contract and complete test modes |
| Account lifecycle | `uv run pytest tests/unit/accounts/test_account_activation.py tests/unit/accounts/test_activation_tokens.py tests/unit/accounts/test_password_management.py tests/unit/accounts/test_password_tokens.py tests/unit/accounts/test_request_validation.py tests/unit/accounts/test_response_timing.py tests/unit/accounts/test_tasks.py tests/unit/accounts/test_user_profiles.py tests/unit/accounts/test_username_management.py tests/unit/accounts/test_username_tokens.py tests/integration/accounts/test_account_activation.py tests/integration/accounts/test_async_email.py tests/integration/accounts/test_password_management.py tests/integration/accounts/test_registration_timing.py tests/integration/accounts/test_user_profiles.py tests/integration/accounts/test_username_management.py -m "not security_timing" --no-cov -q` | REST, SMTP, worker, timing, OpenAPI, and complete test modes |
| Notification delivery | `uv run pytest tests/unit/notifications tests/integration/notifications tests/unit/config/test_routing.py --no-cov -q` | Deployed WebSocket, multi-process delivery, and logs |
| Test architecture | `uv run pytest tests/unit/test_harness.py --no-cov -q` | Exact collection parity and complete dual-mode pass |
| Cross-module | `uv run poe architecture-audit` and `uv run poe typecheck` | Complete quality, security, convention, runtime, and audit gates |

## Findings

No required finding or improvement is confirmed by the Ticket 53 baseline alone. Each area ticket must add a row
before editing code and close it only after interface-level regression evidence and the applicable complete gates.

| ID | Priority | Principle | Module | Evidence and impact | Proposed seam | Disposition | Verification |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `F54-001` | Improvement | ISP | `scripts/manage_platform.py` environment and testing lifecycle functions | `up`, `rebuild`, and `reset` accepted sleep and clock callables they discarded; four testing workflows forwarded the same unused state. Every caller and test had to construct irrelevant dependencies, and lint suppressions hid the breadth. | Remove unused dependencies from each callable and every forwarding layer while retaining clock and sleep seams only in timed setup, polling, and test execution. | Fixed in Ticket 54 without changing public command names, arguments, output, exit codes, Compose operations, cleanup, or resource ownership. | 101 operator tests and the complete 677-test support/Compose layer passed in parallel; Ruff, structured docstrings, mypy, and ty passed; both environments rebuilt and health/ownership passed; convention and security audits passed; the final 28-container window had zero restarts and no unexplained warning-or-higher records. |
| `F55-001` | Required | DIP | `scripts/gen_secrets.py` and SeaweedFS startup configuration | Both SeaweedFS containers logged that SSE-S3 key material would use an implicit plaintext fallback, and unused embedded IAM logged a signing-key error. High-level persistence and security policy therefore depended on concrete vendor defaults outside the generated-secret interface. | Add a distinct persistent KEK passphrase to the manifest and `SECRET_RECIPES`, bind forced rotation to each SeaweedFS data volume, require it at startup, and disable unused IAM. | Fixed in Ticket 55; encrypted environment artifacts contain independently generated values without disclosure, and exact manifest parity rejects obsolete encrypted secret names. | 677 support/Compose tests; strict types; documentation gate; both environments rebuilt and healthy; convention and security audits passed; `2026-09-24T03:56:22.0362093Z..2026-09-24T03:59:26.6154034Z` covered all 28 containers with zero restarts and no unexplained warning-or-higher records. |
| `F56-001` | Improvement | SRP | `src/config/api.py` | Runtime ASGI/Django admission, body limits, exception envelopes, handlers, and routing shared one module with OpenAPI-only constants, evidence maps, schema builders, and post-processing hooks. Runtime changes and documentation-contract changes therefore touched and imported one another despite separate callers and tests. | Move OpenAPI policy and hooks to `config.openapi`; point schema settings and schema tests at that interface; leave runtime API behavior in `config.api` with no compatibility re-export. | Fixed in Ticket 56. Runtime API code no longer owns schema builders, and schema settings resolve only through `config.openapi`. | 378 runtime unit tests passed in parallel; 617 supported integration tests passed with Mailpit lifecycle and clean bounded testing logs; 14 OpenAPI contract tests passed; strict types, docs, Django checks, and security audit passed; live health and schema returned 200; `2026-09-24T04:53:40.3391321Z..2026-09-24T04:53:53.8271397Z` covered 28 containers with zero restarts and no warning-or-higher records. |
| `F57-001` | Required | DIP | `src/accounts/token_authentication.py` | Password-profile classification, lock-free credential verification, account lookup, runtime hardening, and locked revalidation lived inside the token HTTP endpoint module. JWT creation and three account-lifecycle modules imported the transport module to use credential policy, and tests patched transport-owned names to vary policy behavior. | Move credential policy to `accounts.credentials`; make token, JWT, profile, password, and username callers depend on that deep module; remove transport compatibility exports. | Fixed in Ticket 57. Credential policy is one deep module and transport modules expose no compatibility path. | 368 focused authentication tests passed with 100% branch coverage for `accounts.credentials`; six cross-login password-race cases passed; strict types, docs, Django, OpenAPI, and security gates passed; deployed token/JWT OPTIONS returned 200; `2026-09-24T05:27:59.3029436Z..2026-09-24T05:28:09.8890250Z` covered 28 containers with zero restarts and no warning-or-higher records. |
| `F58-001` | Required | OCP | `src/accounts/account_activation.py`, `token_authentication.py`, and `user_profiles.py` | Activation, token, and profile transports independently implemented the same undeclared-field rejection, while activation, password, and username separately normalized and revalidated email values. Lifecycle modules also imported shared policy from profile transport, so one validation-policy change required unrelated endpoint edits. | Move strict request and normalized-email validation to `accounts.request_validation`; make every account serializer depend on that one deep interface; remove every duplicate base and transport compatibility export. | Fixed in Ticket 58. Activation, authentication, profile, password, and username serializers share one strict request and normalized-email policy. | `accounts.request_validation` reached 100% branch coverage; all 276 account unit tests and 617 supported integration tests passed in parallel; both complete modes collected 2,124 tests and passed 2,101 core plus 23 timing tests with 100% branch coverage, zero warnings/skips, exact Mailpit cleanup, ownership, residue, and bounded logs. |
| `F58-002` | Improvement | ISP | `src/accounts/registration_timing.py` | Password-reset and username-reset workflows depended on a registration-named module, callable, docstring, and failure message for their shared public response floor. The interface exposed one workflow's terminology to unrelated callers and made a shared policy appear privately owned. | Replace it with `accounts.response_timing` and a workflow-neutral minimum-response callable; update all callers and tests with no compatibility path. | Fixed in Ticket 58. Registration, password reset, and username reset use one neutral clock/sleep interface. | `accounts.response_timing` reached 100% branch coverage; strict types, docs, and account regressions passed; deployed lifecycle OPTIONS returned 200; `2026-09-24T06:33:01.4677166Z..2026-09-24T06:33:10.1429799Z` covered 28 containers with zero restarts and no warning-or-higher records. |
| `F61-001` | Improvement | DIP | Project runtime import graph | The complete dependency map existed only as audit evidence, so a later production import of tests, a project-private symbol, or a runtime cycle could bypass intended interfaces without an early focused failure. These are objective stable rules the existing AST and pytest tooling can express. | Add failure-focused AST enforcement and a focused `architecture-audit` Poe task inside the complete quality gate; keep type-only edges and historical migrations outside runtime scope. | Fixed in Ticket 61 without adding a dependency or heuristic architecture score. | Seven focused architecture tests, strict types, Ruff, docstrings, and task-wiring self-tests passed. `uv run poe check` collected 2,132 tests and passed 2,109 core plus 23 timing tests at 100% branch coverage, zero warnings/skips, health, ownership, cleanup, and clean bounded logs; all 67 production/operator modules are scanned with no forbidden edge or runtime cycle. |

## Observations

| ID | Module | Concern | Why no code change in Ticket 53 | Revisit trigger |
| --- | --- | --- | --- | --- |
| `O53-001` | `scripts/manage_platform.py` | One module coordinates many operator lifecycles | Ticket 54 found and removed broad unused dependencies, while command dispatch, lifecycle, evidence, cleanup, and failure precedence remained one deep operator interface | Revisit when one policy change repeatedly touches unrelated lifecycle functions or callers need incompatible subsets |
| `O53-002` | `src/accounts/models.py` | Identity and three credential-record types share one ORM module | Tickets 57-58 found shared account persistence, cleanup, indexing, and transaction policy; callers import model classes directly without a broad repository interface | Revisit when one record gains independent persistence policy or callers must depend on unrelated model state |
| `O53-003` | `src/config/logs.py` | Logging, redaction, correlation, streaming finalization, and metrics boundaries interact | Ticket 56 found one end-to-end observability and secrecy contract with interface-level tests; extraction would split ordering and cleanup invariants | Revisit when independent logging actors or invalid formatter/middleware substitution appears |
| `O53-004` | Account recovery modules | Activation, password, and username flows have visible structural similarity | Ticket 58 extracted the proven request and timing seams; token classifications, transactions, retries, and response contracts still differ, so a generic workflow would be speculative | Revisit only when the same policy change must touch multiple workflow implementations |
| `O53-005` | Test suite | 122 modules use shared hooks, factories, probes, and communicators | Ticket 60 found cohesive fixtures, contract-preserving fakes, deterministic hook state, and explicit process/network/storage seams; count and helper count are not violations | Revisit when a shared fixture exposes unrelated state, a fake diverges from production, or parallel cleanup becomes nondeterministic |
| `O53-006` | `accounts.models`, `accounts.managers` | Static typing creates a type-only cycle while runtime imports remain one-way | Ticket 61 confirmed `TYPE_CHECKING` preserves the manager's precise model type without a runtime cycle or service locator | Revisit if the edge becomes executable, blocks typing/module evolution, or expands beyond the model-manager pair |
| `O59-001` | `notifications.websocket` | Host/origin validators, failure boundary, and consumer share one source file | All four classes compose one handshake-to-message ASGI transport lifecycle, expose separate interfaces, and are selected together by the only routing root; splitting files would not reduce caller knowledge or change spread | A second composition root needs only a subset, or one policy change repeatedly touches unrelated classes |
| `O59-002` | `notifications.delivery.publish_notification` | The public publisher acquires the configured channel layer | This function is the sole synchronous-to-channel adapter and hides transport from every caller; injecting a layer into application callers would widen the public interface for one concrete runtime adapter | A second channel adapter or caller-specific transport choice appears |
| `O60-001` | `tests/unit/test_harness.py` | One module enforces many suite-wide structural contracts | The contracts share one actor and failure surface: whether the suite itself can be trusted. Splitting would distribute canonical marker, mirror, stage, and subprocess rules without reducing the interface | Harness changes begin serving independent actors or require unrelated fixtures |
| `O60-002` | `tests/integration/config/runtime_probe.py` | One CLI exposes Mailpit, restart-persistence, and health actions | All actions are operator-controlled assertions over live service transitions, share environment and reporting contracts, and run identically in host/container modes | Another caller needs a stable subset or one action gains independent lifecycle policy |
| `O61-001` | `config.channels` | The channel adapter imports third-party private `_wrap_close` | `channels_redis` hardcodes its loop-layer construction and exposes no public close-hook registration; replacing the import would require copying upstream lifecycle implementation. The dependency is pinned and subscription/reset behavior is exhaustively tested | Upstream exposes a public hook, the pinned implementation changes, or channel cleanup tests detect drift |

## Ticket 62 verification repairs

The definitive gate exposed three test-resilience defects rather than production-contract regressions. Each repair
keeps the asserted behavior and strengthens the supported host/container execution path.

| ID | Verification defect | Root cause | Repair | Preservation evidence |
| --- | --- | --- | --- | --- |
| `V62-001` | A container xdist worker crashed during nested full-suite collection | The harness test started three additional complete pytest collection processes while the supported container suite already ran under xdist | Collect the global security-timing selection once; retain exact 23-case and module membership assertions; rely on the dual-mode runner's independently tested `complete = core + timing` invariant for exhaustive partition proof | Both definitive modes collected 2,132 tests and passed 2,109 core plus 23 timing cases with no worker restart |
| `V62-002` | One-second activation-throttle recovery tests could expire before their second rejection assertion | The tests measured an authoritative PostgreSQL admission window through wall-clock request duration and sleep | Use one-minute windows and age the authoritative `LoginThrottleEvent` rows beyond the window before the recovery request | Account activation passed in both complete modes; the integration lifecycle audit passed; production throttle policy was unchanged |
| `V62-003` | One WebSocket integration receive could exceed ten seconds only under the loaded complete host run | The test helper's ten-second output deadline was below the supported suite's observed scheduling delay even though the project-wide network timeout remained bounded | Raise only the integration receive helper to 20 seconds, still below the enforced 60-second pytest timeout | Every WebSocket contract case passed in both complete modes; the deployed credential-absent socket closed with `4401` |

## Ticket 62 final verification

The final source-matched verification sequence passed without weakening any gate:

```console
uv sync --all-groups --frozen
uv run poe development-health
uv run poe testing-health
uv run poe docker-audit
uv run poe testing-test-both
uv run poe testing-integration-audit
uv run poe check
uv run poe convention-audit
uv run poe security-audit
```

The definitive dual-mode run collected 2,132 tests in each mode. Each mode passed 2,109 core tests and 23
security-timing tests with 100% branch coverage, zero warnings, zero skips, no worker restart, exact temporary
Mailpit ownership and removal, and clean bounded testing logs. Its windows were:

- container: `2026-09-24T12:22:43.705285Z..2026-09-24T12:32:33.934843Z`;
- host: `2026-09-24T12:32:33.934879Z..2026-09-24T12:56:43.297050Z`.

The subsequent complete host quality gate again passed 2,109 core and 23 timing tests at 100% branch coverage.
Its bounded host window was
`2026-09-24T13:06:43.436156Z..2026-09-24T13:31:36.429676Z`. The integration audit passed five SMTP cases in both
modes, Mailpit recreation and deletion, degraded readiness, recovery, ownership, residue, and image checks.
Convention, security, development health, testing health, and Docker ownership audits all passed against 22
development and six testing containers.

The final deployed exercise proved:

- `/health/` returned `ready` with seven working checks;
- the live OpenAPI document matched `docs/api/openapi-v1.yaml` and retained exactly 15 paths and 19 operations;
- Swagger UI and ReDoc returned success through Traefik;
- a credential-absent notification socket closed with `4401`;
- the Celery worker completed its transient broker round trip;
- `uv run poe help` preserved the operator command interface.

The bounded window
`2026-09-24T13:41:57.5089750Z..2026-09-24T13:42:03.6444339Z` covered all 28 project containers. Every container
was running, every health-checked container was healthy, every restart count was zero, and every log window had
zero warning-or-higher records.

The final local image policy binds:

- `localforge/django:0.1.0` image
  `sha256:d7479c2b34414da46e485dfccd64a71a696c11839579ba1e83365c76597d389f`, artifact
  `sha256:fd2f630db58f1366fb2e47f45c2854e93ef332e9e00844fd9b59c45c56bd8f5e`;
- `localforge/django-test:0.1.0` image
  `sha256:f6ff5013097e25b8eca91b0b6942b42a392b82e717bdab9657bf6d9de790e4b0`, artifact
  `sha256:69254a3ab8dd4a230701e6430dd3f392f13a91f9640c40e34a79247123eb266b`;
- `localforge/pgbackrest:18.6` image
  `sha256:78f396863a1c8b5e417d448d3a7c549f12d8d8ad0b24af0603ffa518739914e2`, artifact
  `sha256:a25eeb9b6eebb0efc9df90dd1a166709eb95cfe5c5ca5369afc5838bfbd10c1f`.

Independent retained GPT-5.6 Terra and GPT-5.6 Sol auditors reviewed the identical complete Ticket 62 package.
Both reported no legitimate finding.

Ticket 63 excludes the Phase 8 findings ledger and handover from the test-image build context, matching the
existing Phase 7 evidence exclusion. This keeps the reviewed image identity reproducible when only final evidence
changes. The 164 Compose foundation tests passed, a repeated no-edit build retained the reviewed identity, and the
complete remediated container mode passed 2,109 core plus 23 timing tests at 100% coverage. Its bounded window
`2026-09-24T14:00:08.307542Z..2026-09-24T14:10:07.556280Z` had clean ownership, teardown, residue, and logs.
Security, testing health, and project Docker ownership passed afterwards.

## Phase 8 disposition

All 189 scoped Python modules have one cluster, one area owner, a caller/interface description, composition-root or
adapter disposition, and an evaluation of SRP, OCP, LSP, ISP, and DIP. Tickets 54-61 replace every `Review` entry
with evidence-backed clear, fixed, not-applicable, or observation dispositions. The seven confirmed SOLID findings
are fixed, the eleven observations have explicit revisit triggers, and Ticket 62's three verification hardenings
preserve production behavior while making the complete supported gate reliable.
