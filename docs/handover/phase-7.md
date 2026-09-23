# Phase 7 final verification and handover

Verified **2026-09-22**. This report closes the 52-ticket build. It records the final built scope, clean-checkout
rehearsal, test and runtime evidence, accepted risks, review dates, and deliberately deferred work.

## Built scope

LocalForge provides exactly two Docker Compose environments:

- `localforge-dev`: the complete 22-container development platform with dashboards and operator surfaces.
- `localforge-test`: the six-container headless testing platform, plus profile-gated Mailpit only during its
  lifecycle audit.

The development platform includes primary and replica PostgreSQL, pgBackRest, pgAdmin, separate Valkey cache and
Channels instances, RabbitMQ, a Celery worker and database scheduler, Flower, SeaweedFS, Mailpit, Traefik,
Prometheus, Grafana, Loki, Alloy, cAdvisor, PostgreSQL and Valkey exporters, and Django under Uvicorn.

The application surface remains bounded to:

- `/health/`.
- `/api/schema/`, `/api/schema/swagger-ui/`, and `/api/schema/redoc/`.
- The 14 fixed account, token, and JWT operations under `/api/v1/`.
- The authenticated notification socket at `/ws/notifications/`.

No production environment, Kubernetes manifest, scaling rule, or application route outside that fixed surface was
introduced.

## Clean-checkout rehearsal

The rehearsal began with zero LocalForge containers, networks, volumes, or local images, proven by:

```console
uv run poe docker-clean-check
```

A fresh local clone at commit `ca70d895a5b8409aabefd73f13688302ffd11f0d`, overlaid only with the exact
Ticket 52 candidate diff, then followed the README setup:

```console
uv sync --all-groups --frozen
uv run poe help
uv run poe environments-setup
```

Dependency installation completed in 45.58 seconds. Environment setup decrypted the two committed SOPS files,
generated the host testing file, built the three absent local images, started both projects, waited for readiness,
and passed the Docker ownership audit in 207.508 seconds. No undocumented setup step was required.

The first dual-mode run found one runtime-only defect despite all 4,178 test executions passing: Kombu created each
native delayed-delivery queue before its next dead-letter exchange on the fresh broker, producing 28 warnings.
`scripts/prepare_broker.py` now declares all 29 durable topic exchanges before Django, Celery, or the test runner
becomes healthy. A fresh testing-volume reset then passed the complete dual-mode gate with clean broker windows.

## Verification commands and results

| Gate | Command | Result |
| --- | --- | --- |
| Clean runtime precondition | `uv run poe docker-clean-check` | pass; zero LocalForge resources |
| Written setup | `uv sync --all-groups --frozen`; `uv run poe help`; `uv run poe environments-setup` | pass; 207.508-second setup |
| Dual-mode parity | `uv run poe testing-test-both` | pass; 2,103 collected per mode |
| Container suite | dual-mode container stage | 2,075 core passed, 5 intentional SMTP-profile skips, 23 timing passed, 100% branch coverage, 529.440 seconds |
| Host suite | dual-mode host stage | 2,075 core passed, 5 intentional SMTP-profile skips, 23 timing passed, 100% branch coverage, 1,359.211 seconds |
| Dual-mode total | `uv run poe testing-test-both` | pass; 1,963.666 seconds, equal counts, exact container set, ownership, residue, and bounded logs |
| Full quality | `uv run poe check` | pass; Django checks, migrations, OpenAPI, Ruff, formatting, docstrings, mypy, ty, 2,075 core and 23 timing tests |
| Final source-matched container | `uv run poe testing-test-container` | pass; 2,075 core, 5 skips, 23 timing, 100% coverage, 569.220 seconds, clean bounded logs |
| Service lifecycle | `uv run poe testing-integration-audit` | pass; Mailpit host/container persistence and deletion plus real degraded and recovered readiness |
| Security | `uv run poe security-audit` | pass; deployment, history, dependencies, immutable images, exposure, accounts, and runtime secrets |
| Conventions | `uv run poe convention-audit` | pass for development and testing |
| Docker ownership | `uv run poe docker-audit` | pass; exact project-scoped containers, networks, volumes, images, labels, and health |
| Health | `uv run poe development-health`; `uv run poe testing-health` | pass |

The host timing stage is deliberately statistical and dominated the time budget. The container and host core counts
plus their 23 timing cases equal the same complete collection, and both core stages ran with parallel workers.

## HTTP, schema, and WebSocket contract

The deployed development entry point was exercised through Traefik with its registered Host rule:

- The live OpenAPI document was structurally equal to `docs/api/openapi-v1.yaml`.
- The schema contained exactly 15 paths and 19 operations.
- Every operation was invoked once through the deployed route and returned a status documented for that operation.
- `/health/` returned `200`, `ready`, and seven `working` dependency checks for GET and HEAD.
- Swagger UI and ReDoc returned `200`.
- A credential-free public WebSocket connection closed with `4401`.

The complete integration suite independently observes every response status in the committed OpenAPI contract,
including middleware, parsing, content negotiation, throttling, CSRF, authentication, and framework error paths.
The WebSocket integration and pinned-Uvicorn suites observe all private application close codes `4400` through
`4408` and `4500`, transport codes `1002`, `1007`, `1009`, `1011`, and `1012`, plus handshake HTTP `400` and `404`.
The committed HTTP schema and WebSocket tables are generated or asserted against the same runtime authorities used
by those tests.

## Runtime and persistence evidence

The final platform retained the Ticket 51 outage and restart evidence:

- Primary PostgreSQL, replica PostgreSQL, cache Valkey, Channels Valkey, RabbitMQ, SeaweedFS, and Mailpit were each
  stopped independently; deployed readiness returned `503` and recovered to `200`.
- A complete stack restart preserved authoritative and replicated database state, cache state, private S3 bytes,
  Mailpit messages, and a durable RabbitMQ quorum message.
- RabbitMQ publisher confirmation failed while the broker was stopped and succeeded after recovery.
- Internal-only networks failed active external DNS probes.

The final exercised observation window was
`2026-09-23T04:36:29.055411Z..2026-09-23T04:38:29.726310Z`. It covered all 28 running project containers while
requesting health, the OpenAPI document, both documentation UIs, and a public credential-free WebSocket close.
Every container retained its restart count and the window contained zero unexplained warning, error, or critical
records.

A newly created RabbitMQ data volume emits two expected first-boot warnings before health: empty classic peer
discovery and persistent message-store index creation. ADR-0008 bounds them to first initialization. The delayed
delivery warnings discovered by the first rehearsal were fixed, not accepted.

The extended observation also found cAdvisor periodically logging missing machine identity and OOM-event sources.
The final service receives one deterministic non-secret machine-id at both lookup paths and the exact read-only
`/dev/kmsg` device without privileged mode. The convention audit enforces both binds and the sole device grant.

## Security findings, accepted risks, and review dates

Ticket 51 fixed unauthenticated Mailpit access, private SeaweedFS and observability publications, wildcard host
bindings, unnecessary egress memberships, the public Traefik ping, incomplete secret scans, mutable scan identity,
quorum publication confirmation, legacy queue upgrade drift, and image build drift. The exact reviewed image
snapshot remains in `docs/security/image-vulnerability-policy.json`.

Final reproducible local image identities:

- `localforge/django:0.1.0`:
  `sha256:e7e0ba19e8d3cc0311259e05844452e559cca00b57c570a749901c3e19a682ec`.
- `localforge/django-test:0.1.0`:
  `sha256:3d53be1d68197ec1ff1f3dfa2eb9c3a0f68505c9defeb7efec6232562c54c997`.
- `localforge/pgbackrest:18.6`:
  `sha256:716730d41384fbed2da3c686a40c6ede6b86ec17deb882f0f593646ed39de3f3`.

Repeated no-edit builds after the final tests retained all three identities. Building the exact candidate from the
original worktree and the clean candidate checkout produced the same Django and test image IDs. The Dockerfile
normalizes copied modes and timestamps, while recursive ignore rules exclude generated bytecode, caches, package
metadata, the handover report, and the image policy. Evidence-only updates therefore cannot change the artifact
they describe.

Accepted risks:

- Docker socket readers and the root Traefik process retain daemon-level authority on this local single-user
  platform. Review **2026-12-22**.
- Plain HTTP is retained for the offline local boundary; the exact four Django deployment warnings remain visible
  and enforced.
- Valkey exporter credentials remain visible to Docker administrators through process arguments; Docker daemon
  authority already grants the same credentials and control.
- The Autobahn test fixture and Debian PostgreSQL snake-oil key remain accepted non-credential image findings.
- Current upstream image vulnerabilities recorded by the policy are accepted until replacement images exist.
  Review **2026-10-06**.

RabbitMQ 4.3 reaches end of community support on **2026-11-30**. Review and move to a supported series by that date.

## Dependency and release-lag disposition

No runtime dependency pin moved during the final verification ticket. ADR-0016 records every release-lag gate as
resolved against released artifacts: Celery, channels-redis, django-storages, and SimpleJWT all passed their real
end-to-end seams on Python 3.14 and Django 6.0. No Git dependency or first-party fallback was taken. Flower 2.1.0
was installed by Ticket 44 and passed its authenticated dashboard and failure-isolation gates.

## Deferred work

The following work is deliberately outside this platform:

- A production environment.
- Kubernetes resources or a running cluster.
- Replica counts, autoscaling, rolling deployment, and other orchestrator behavior.
- Any HTTP route, WebSocket route, model, or feature outside the fixed application surface.
- Cloud services or runtime internet access.

Work stops at this handover.
