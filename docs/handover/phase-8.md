# Phase 8 SOLID architecture handover

> Historical path note: this handover records pre-Phase-10 evidence. Unqualified source, test, script, and Poe paths
> refer to the backend root at that time; current paths use `backend/` and root wrappers.

Reverified **2026-09-24**. This report closes the evidence-led SOLID audit without expanding LocalForge's bounded
application or platform surface.

## Outcome

Phase 8 audited 189 project-owned Python modules: 67 production or operator modules and 122 test modules. Every
module belongs to exactly one evidence cluster with named callers, interface, composition root or adapter
disposition, and an SRP, OCP, LSP, ISP, and DIP result.

The audit confirmed seven SOLID findings, fixed all seven, retained eleven justified observations with explicit
revisit triggers, and made three final test-resilience repairs. No route, status, response body, close code,
protocol, service, environment, dependency, operator command, resource name, or offline-boundary rule was added or
removed.

## Audited clusters

| Ticket | Area and clusters | Modules | SRP | OCP | LSP | ISP | DIP |
| --- | --- | ---: | --- | --- | --- | --- | --- |
| 54 | Operator orchestration, `54-OP1` | 1 | Lifecycle policy remains cohesive behind the command interface | Fixed command registry remains data-driven without a plugin system | Runners, probes, clocks, and records retain their contracts | Fixed unused lifecycle dependencies | Composition selects concretes; policy accepts only dependencies that vary |
| 55 | Support scripts, `55-S1` to `55-S7` | 12 | Each script owns one command policy or lifecycle | Existing registries express real variation | Process, probe, storage, broker, and filesystem adapters preserve errors and outputs | Script interfaces expose only used operations | External tools and clients remain behind command seams; SeaweedFS persistence policy is generated |
| 56 | Django runtime, `56-R1` to `56-R4` | 24 | Runtime HTTP and OpenAPI policy now have separate owners | Governed settings, route, status, and task variation remains explicit | Framework hooks preserve protocol, cleanup, and error contracts | Runtime callers no longer receive schema-only policy | Concrete framework and infrastructure adapters remain at composition roots |
| 57 | Account authentication, `57-A1` to `57-A3` | 14 | Identity, credential policy, transports, and admission have distinct owners | Token and JWT variation uses explicit adapters | ORM, DRF, JWT, token, and throttle substitutes preserve accepted domains | Lifecycle callers depend on credential operations rather than transport modules | Credential policy is a deep module above hashing, ORM, and token stores |
| 58 | Account lifecycle, `58-L1` to `58-L4` | 10 | Activation, password, username, request, timing, profile, and task policy have evidence-backed owners | Shared request and timing policy is consolidated; workflow-specific rules remain local | Tokens, serializers, tasks, and transactions preserve classifications and postconditions | Shared interfaces use workflow-neutral terminology and focused inputs | Clock, persistence, task, mail, and notification details stay behind lifecycle seams |
| 59 | Notification delivery, `59-N1` to `59-N2` | 6 | Admission, authentication, protocol, delivery, and transport responsibilities remain explicit | Fixed event tables and routing cover governed variation | ASGI and channel adapters preserve ordering, cancellation, close, and cleanup contracts | Publishers and consumers expose only event or authenticated-scope state | Channel-layer acquisition stays inside the sole publisher adapter |
| 60 | Test architecture, `60-T1` to `60-T7` | 122 | Fixtures, factories, probes, communicators, and suites mirror one actor or production owner | Shared test policy grows through markers and focused helpers | Fakes preserve production-visible contracts | Fixtures expose only required state; suite policy remains one harness interface | Process, clock, service, storage, and transport variation is explicit |
| 61 | Cross-module graph, all clusters | 189 | Existing owners remain unchanged | Stable dependency rules extend through focused checks | Import analysis preserves executable versus type-only semantics | Project-private imports cannot become accidental interfaces | Runtime imports of tests, private cross-module symbols, and cycles now fail the quality gate |

The durable per-module evidence is in [the findings ledger](../architecture/solid-findings.md). Historical migrations
and installed `.agents/` skills remain outside the governed inventory. The executable production/operator graph
has no project-owned import cycle; the `accounts.models` and `accounts.managers` edge is type-only.

## Confirmed findings and repairs

| Finding | Root cause and impact | Changed seam | Preservation evidence | Final disposition |
| --- | --- | --- | --- | --- |
| `F54-001` | Environment and testing lifecycles forwarded unused sleep and clock state, widening every caller | Removed unused dependencies while retaining them only for timed setup, polling, and execution | 101 operator tests, 677 support/Compose tests, both environments, audits, and a clean 28-container window | Fixed without changing commands, output, exit codes, cleanup, or ownership |
| `F55-001` | SeaweedFS persistence depended on an implicit plaintext SSE fallback and unused IAM startup behavior | Generated a distinct KEK passphrase, bound rotation to data volumes, required it at startup, and disabled IAM | Secret-manifest parity, encrypted artifacts, both rebuilt environments, security/convention audits, clean logs | Fixed; persistence security is owned by generated platform policy |
| `F56-001` | Runtime HTTP behavior and OpenAPI-only builders shared `config.api` despite separate callers | Moved schema constants, evidence, builders, and hooks to `config.openapi` with no compatibility export | 378 runtime unit tests, 617 integration tests, OpenAPI contracts, deployed schema, strict types, clean logs | Fixed; runtime and documentation policy are separate deep modules |
| `F57-001` | JWT and lifecycle code imported token transport to reuse password verification and hardening | Extracted `accounts.credentials` and made all authentication/lifecycle callers depend on it | 368 focused tests, credential branch coverage, race cases, deployed token/JWT routes, security and runtime gates | Fixed; credential policy is transport-independent |
| `F58-001` | Strict fields and normalized-email validation were duplicated and lifecycle policy was transport-owned | Consolidated both policies in `accounts.request_validation` with no compatibility path | 276 account unit tests, 617 integration tests, complete dual-mode suites, OpenAPI evidence | Fixed; one shared request-policy interface serves all proven callers |
| `F58-002` | Password and username recovery depended on registration-specific timing terminology | Replaced it with workflow-neutral `accounts.response_timing` | Direct branch coverage, strict types, complete account and deployed lifecycle evidence | Fixed; the interface now describes the shared invariant |
| `F61-001` | Intended dependency directions existed only in prose and could regress silently | Added `tests/unit/test_architecture.py` and `uv run poe architecture-audit` to `uv run poe check` | Seven failure-focused tests, all 67 executable production/operator modules, complete quality gate | Fixed; objective import rules are machine-enforced without architecture scores |

No compatibility layer was retained for any extraction. The deletion test confirms each new module earns leverage:
removing it would redistribute credential, request-validation, response-timing, or OpenAPI policy across multiple
callers.

## Justified observations and rejected abstractions

| IDs | Concern | Why no further module was introduced | Revisit trigger |
| --- | --- | --- | --- |
| `O53-001` | `scripts.manage_platform` coordinates many lifecycles | Command dispatch, evidence, cleanup, and failure precedence form one deep operator interface after unused dependencies were removed | Independent callers need incompatible subsets or changes repeatedly cross unrelated lifecycles |
| `O53-002`, `O53-006` | Account records share one ORM module and one model-manager type-only cycle | Persistence, constraints, typing, and transaction policy remain shared; a repository abstraction or service locator would add caller knowledge | One record gains independent persistence policy or the edge becomes executable |
| `O53-003` | Logging combines redaction, correlation, streaming finalization, and metrics boundaries | Ordering, secrecy, finalization, and cleanup are one end-to-end contract | Independent logging actors or invalid substitution appears |
| `O53-004` | Activation, password, and username recovery look structurally similar | Proven request and timing seams were extracted; token classes, transactions, retries, and responses still differ | The same policy change must edit multiple workflow implementations |
| `O53-005`, `O60-001` | The test suite and harness have many modules and rules | Count is not a violation; fixtures and the harness retain cohesive actors and explicit failure surfaces | Shared fixtures expose unrelated state or suite policy serves independent actors |
| `O59-001` | WebSocket validators, failure boundary, and consumer share one file | They compose one handshake-to-message lifecycle and are selected by one routing root | A second root needs only a subset or changes spread across unrelated classes |
| `O59-002` | The publisher acquires the configured channel layer | It is the sole synchronous-to-channel adapter; injecting the layer into callers would widen the interface | A second transport adapter or caller-specific choice appears |
| `O60-002` | One runtime probe CLI exposes several live transitions | Mailpit, persistence, and health actions share operator, environment, and reporting contracts | Another caller needs a stable subset or one action gains independent policy |
| `O61-001` | `config.channels` imports pinned private `_wrap_close` | Upstream exposes no public close hook; replacement would copy upstream lifecycle implementation | Upstream adds a public hook, the pin changes, or cleanup tests detect drift |

The audit rejected plugin systems for fixed routes and commands, repository wrappers around one Django ORM,
generic recovery workflows without uniform semantics, injected channel layers for one concrete publisher, and
file splitting that would not reduce caller knowledge or change spread.

## Final verification repairs

Ticket 62 corrected three defects exposed only by the definitive loaded gate:

- `V62-001`: replaced three nested full-suite collections with one exact security-timing collection while the
  independently tested runner continues to prove `complete = core + timing`.
- `V62-002`: replaced one-second sleep-based throttle recovery with one-minute windows and authoritative
  `LoginThrottleEvent` aging.
- `V62-003`: raised the integration WebSocket receive deadline from 10 to 20 seconds while retaining the
  project-wide 60-second test timeout.

Production throttle, WebSocket, and collection contracts did not change.

Ticket 63's first independent review found that the new findings ledger and handover still entered the test-image
build context, making the recorded final identity self-referential, and that the build-plan inventory omitted the
architecture test. The remediation excludes both Phase 8 evidence files from Docker context, enforces the
exclusions in the Compose foundation test, and inventories `tests/unit/test_architecture.py` explicitly.

The 164 Compose foundation tests passed. A repeated no-edit `uv run poe testing-build` retained image
`sha256:f6ff5013097e25b8eca91b0b6942b42a392b82e717bdab9657bf6d9de790e4b0`. The rebuilt
`uv run poe testing-test-container` collected 2,132 tests and passed 2,109 core plus 23 timing tests at 100%
coverage with zero warnings, zero skips, no worker restart, and exact temporary Mailpit cleanup. Its bounded window
`2026-09-24T14:00:08.307542Z..2026-09-24T14:10:07.556280Z` passed container ownership, residue, and log checks.
The security audit, testing health, and complete Docker ownership audit passed afterwards.

## Verification commands and results

The written sequence was rehearsed from this documented precondition: Docker was running, generated local
environment files existed, and both environments had been rebuilt from the current source. No undocumented command
was required.

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

| Gate | Result |
| --- | --- |
| Frozen dependency sync | Passed without changing the lock or dependency surface |
| Dual-mode parity | 2,132 collected in each mode; 2,109 core and 23 security-timing tests passed per mode |
| Coverage and diagnostics | 100% branch coverage; zero pytest warnings, zero skips, no xdist worker restart |
| Container mode | Passed with exact temporary Mailpit ownership, teardown, residue, and bounded logs |
| Host mode | Passed with the same collection/pass counts, Mailpit lifecycle, ownership, and bounded logs |
| Integration lifecycle | Five SMTP cases passed in both modes; Mailpit recreation/deletion and real degraded/recovered readiness passed |
| Full quality | Django checks, migrations, OpenAPI, Ruff format/lint, structured docstrings, mypy strict, ty, architecture audit, and complete host suite passed |
| Convention and security | Both audits passed without a Phase 8 exclusion or accepted finding |
| Runtime truth | Development health, testing health, and exact Docker ownership passed for 22 plus six containers |

The definitive dual-mode windows were:

- container: `2026-09-24T12:22:43.705285Z..2026-09-24T12:32:33.934843Z`;
- host: `2026-09-24T12:32:33.934879Z..2026-09-24T12:56:43.297050Z`.

The subsequent complete host quality window was
`2026-09-24T13:06:43.436156Z..2026-09-24T13:31:36.429676Z`.

## Deployed contract and logs

The final deployed exercise through Traefik proved:

- `/health/` returned `ready` with seven working dependency checks;
- the live OpenAPI document exactly matched `docs/api/openapi-v1.yaml`;
- the schema retained 15 paths and 19 operations;
- Swagger UI and ReDoc returned success;
- a credential-absent notification socket closed with `4401`;
- the Celery worker completed a transient broker round trip;
- `uv run poe help` preserved the operator command interface.

The bounded exercise window was
`2026-09-24T13:41:57.5089750Z..2026-09-24T13:42:03.6444339Z`. All 28 project containers were running, every
health-checked container was healthy, every restart count was zero, and every bounded log window contained zero
warning-or-higher records.

## Security and immutable images

The final security audit passed deployment, history, dependency, immutable-image, exposure, account, and runtime
secret checks. Phase 7's accepted risks and review dates remain unchanged. The handover, findings ledger, and image
policy are excluded from test-image context; a no-edit build after their final identity update retained the same
test-image ID.

| Image | Image ID | Artifact ID |
| --- | --- | --- |
| `localforge/django:0.1.0` | `sha256:d7479c2b34414da46e485dfccd64a71a696c11839579ba1e83365c76597d389f` | `sha256:fd2f630db58f1366fb2e47f45c2854e93ef332e9e00844fd9b59c45c56bd8f5e` |
| `localforge/django-test:0.1.0` | `sha256:f6ff5013097e25b8eca91b0b6942b42a392b82e717bdab9657bf6d9de790e4b0` | `sha256:69254a3ab8dd4a230701e6430dd3f392f13a91f9640c40e34a79247123eb266b` |
| `localforge/pgbackrest:18.6` | `sha256:78f396863a1c8b5e417d448d3a7c549f12d8d8ad0b24af0603ffa518739914e2` | `sha256:a25eeb9b6eebb0efc9df90dd1a166709eb95cfe5c5ca5369afc5838bfbd10c1f` |

## Tickets, inventory, and independent audits

Phase 8 contains eleven tickets, 53 through 63. Tickets 53-62 are complete; Ticket 63 is this final handover. The
build-plan inventory now includes the audit plan, findings ledger, handover, extracted OpenAPI, credential, request,
and response-timing modules, plus the architecture enforcement test. No unlisted runtime file was introduced.

Retained GPT-5.6 Terra and GPT-5.6 Sol auditors reviewed every completed ticket. Ticket 62's identical complete
package received two clean reports with no legitimate finding. Ticket 63's first Terra review was clean. Sol found
the self-referential test-image context and missing architecture-test inventory; both findings were remediated and
their affected build, test, security, health, ownership, and log gates passed. Both retained auditors then reviewed
the identical revised package and reported no legitimate finding.

## Stop condition

Phase 8 stops after this handover. A future feature, route, protocol, service, environment, dependency, or
architecture change starts with a new governing document and ticket set rather than extending this completed phase.
