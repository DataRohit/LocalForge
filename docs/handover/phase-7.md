# Phase 7 final verification and handover

Reverified **2026-09-23** after repairing the manual handover failures. This report records the final built scope,
test and runtime evidence, accepted risks, review dates, and the one destructive clean-room step left to upstream
because the current LocalForge volumes were preserved.

## Built scope

LocalForge provides exactly two Docker Compose environments:

- `localforge-dev`: the complete 22-container development platform with dashboards and operator surfaces.
- `localforge-test`: the six-container headless testing platform, plus profile-gated Mailpit during every complete
  suite and the lifecycle audit.

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

## Setup and clean-room disposition

`docker-clean-check` is an assertion, not a cleanup command. It correctly fails when either LocalForge project or
its images, networks, or volumes already exist. The supplied manual run began on an active platform, so those
failures were expected and did not describe inventory drift.

The repaired ordinary setup path was run twice without edits:

```console
uv sync --all-groups --frozen
uv run poe help
uv run poe environments-setup --proxy-only
uv run poe environments-setup --proxy-only
```

Setup now pulls only missing external images and always rebuilds all three local images through cached deterministic
layers before starting with `--no-build`. The final auditor-remediated run completed in 165.902 seconds and the
repeated cached run in 25.499 seconds. Both passed health and Docker ownership. Consecutive no-edit builds produced
identical image IDs.

A new destructive zero-resource rehearsal was not run because it would delete the current LocalForge named volumes
and the user was unavailable to approve data loss. Upstream should perform that final rehearsal on a disposable
machine or after explicitly approving project-scoped volume removal, then require `docker-clean-check` to pass
before following the setup commands above.

## Verification commands and results

| Gate | Command | Result |
| --- | --- | --- |
| Clean runtime precondition | `uv run poe docker-clean-check` | not run destructively; expected to fail while the preserved platform exists |
| Written setup | `uv sync --all-groups --frozen`; `uv run poe help`; `uv run poe environments-setup --proxy-only` | pass twice; 165.902 seconds then 25.499 seconds |
| Dual-mode parity | `uv run poe testing-test-both` | pass; 2,112 collected per mode |
| Container suite | dual-mode container stage | 2,089 core passed, 23 timing passed, zero skips/warnings, 100% branch coverage, 524.892 seconds |
| Host suite | dual-mode host stage | 2,089 core passed, 23 timing passed, zero skips/warnings, 100% branch coverage, 1,333.515 seconds |
| Dual-mode total | `uv run poe testing-test-both` | pass; 1,919.697 seconds, equal counts, exact temporary Mailpit lifecycle, ownership, residue, and bounded logs |
| Full quality | `uv run poe check` | pass; Django checks, migrations, OpenAPI, Ruff, formatting, docstrings, mypy, ty, 2,089 core and 23 timing tests |
| Final source-matched container | `uv run poe testing-test-container` | pass; 2,089 core, 23 timing, zero skips/warnings, 100% coverage, clean bounded logs |
| Service lifecycle | `uv run poe testing-integration-audit` | pass; five SMTP cases in both modes, Mailpit persistence/deletion, and real degraded and recovered readiness |
| Security | `uv run poe security-audit` | pass; deployment, history, dependencies, immutable images, exposure, accounts, and runtime secrets |
| Conventions | `uv run poe convention-audit` | pass for proxy-only development and testing; mode derived from live Compose labels |
| Docker ownership | `uv run poe docker-audit` | pass; exact project-scoped containers, networks, volumes, images, labels, and health |
| Health | `uv run poe development-health`; `uv run poe testing-health` | pass |

Pytest treats warnings as errors. The former five SMTP skips now run in every complete mode while Mailpit exists
temporarily, and the command clears and removes the profile container before returning. The host timing stage is
deliberately statistical and dominated the time budget.

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
`2026-09-23T18:28:06.6914550Z..2026-09-23T18:28:12.6005713Z`. It covered all 28 running project containers while
requesting health, the OpenAPI document, both documentation UIs, a public credential-free WebSocket close, and a
real Celery worker/result-backend health round trip. Every affected container retained restart count zero and all
28 bounded log windows contained zero warning, error, or critical records.

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
  `sha256:3a8d862b4f56ec4e0c554654931f69a55f511d1a32f5f2fb47dd855c414e43ae`.
- `localforge/django-test:0.1.0`:
  `sha256:c6d1844a0224b00662e3df15fc6d02f71ae90369d84620adbb2b18696451aebd`.
- `localforge/pgbackrest:18.6`:
  `sha256:7e3335a60ce552e722557406e55a3ec65f4974785d04bba1391138dd50301d29`.

Repeated no-edit builds after formatting and documentation reconciliation retained all three identities. The
Dockerfile normalizes copied modes and timestamps, while recursive ignore rules exclude generated bytecode, caches,
package metadata, the handover report, and the image policy. Evidence-only updates therefore cannot change the
artifact they describe.

Accepted risks:

- Docker socket readers and the root Traefik process retain daemon-level authority on this local single-user
  platform. Review **2026-12-22**.
- Plain HTTP is retained for the offline local boundary; the exact four Django deployment warnings remain visible
  and enforced.
- Valkey exporter credentials remain visible to Docker administrators through process arguments; Docker daemon
  authority already grants the same credentials and control.
- The Debian PostgreSQL snake-oil key remains the sole accepted non-credential image finding. Removing the
  Daphne/Autobahn test dependency removed the prior test-image fixture.
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

One in-scope handover proof remains blocked on explicit approval to destroy current LocalForge data: the
zero-resource clean-clone rehearsal described above. All non-destructive implementation, test, security, convention,
health, ownership, deterministic-build, and runtime-log gates pass.
