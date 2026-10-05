# Phase 7 security and reliability audit

Reviewed **2026-09-26**. Re-run the complete audit with `make security-audit`; runtime-affecting changes also require
both health gates, `make convention-audit`, deployed behavior exercise, and bounded
log review.

## Findings and disposition

| Finding | Severity | Disposition |
| --- | --- | --- |
| Mailpit exposed captured activation and recovery material without authentication | high | fixed: `MP_UI_AUTH` is generated, UI and API require Basic authentication, unauthenticated API access is tested, and both host ports bind to loopback |
| SeaweedFS master, volume, and filer HTTP surfaces bypassed S3 authorization | high | fixed: native administration ports are container-internal; host mode proves master and filer ports refuse connections; only the signed S3 gateway is published to loopback |
| Loki, Alloy, Prometheus, cAdvisor, and exporter APIs were host-published without authentication | medium | fixed: direct publications were removed; authenticated Grafana is the operator surface |
| Unpublished observability services retained internet egress through the access zone | high | fixed: every service without a host publication now has internal-only network membership, enforced by source and live convention audits |
| Every other Compose publication listened on all host interfaces | medium | fixed: every remaining development and testing publication binds to `127.0.0.1`; the convention auditor enforces the exact interface |
| Traefik `/ping` answered without authentication on the dashboard publication | medium | fixed: `/ping` uses an un-published internal health entrypoint |
| Celery recovery emitted RabbitMQ `global_qos` errors despite succeeding | medium | fixed: consumed queues use topic-backed quorum queues and quorum detection; broker outage recovery is warning/error/critical-free |
| Quorum task publication lacked broker confirmation | high | fixed: `confirm_publish` is mandatory in broker transport options, and a stopped-broker publish fails before recovery and confirmed delivery |
| Existing durable classic/direct queues could not be redeclared as quorum/topic | high | fixed: v2 queue/exchange names are source constants that ignore preserved legacy env values; worker startup never deletes old resources, which remain for an explicitly fenced manual drain and removal |
| Docker socket readers retain daemon-level authority despite read-only mounts | high | accepted for this local single-user platform in ADR-0007 and ADR-0010; review due **2026-12-22** |
| Traefik runs as root in the official image | medium | accepted with the same local-only boundary and review date as the socket risk |
| Plain HTTP makes HSTS, SSL redirect, and secure-cookie checks inapplicable | medium | accepted by the offline local deployment shape; the four exact Django deployment warnings remain visible and machine-enforced rather than silenced |
| Valkey exporter credentials are visible to Docker administrators through container process arguments | low | accepted: Docker daemon access already grants the credential and root-equivalent control; host publications are removed and logs/image layers are scanned for disclosure |

## Authentication and exposure evidence

The runtime security scope requires unauthenticated requests to return `401` for Traefik dashboard API, RabbitMQ
management API, Flower API, Mailpit API, and Grafana search. pgAdmin must return exactly `302` with
`Location: /login?next=/browser/`; a `200`, alternate redirect, or database-shaped body is a failure. RabbitMQ must
list exactly the generated `localforge_broker` account and must not contain `guest`.

Host ports 8082, 8888, 9333, 9090, 3100, 12345, 8090, 9187, 9121, 9122, 28888, and 29333 must refuse connections.
These cover the former Traefik ping publication, both environments' SeaweedFS master/filer publications,
Prometheus, Loki, Alloy, cAdvisor, PostgreSQL exporter, and both Valkey exporters.

All published application, database, cache, broker, SMTP, S3, and authenticated dashboard ports are loopback-only.
The convention audit additionally launches a pinned disposable probe on every internal network and requires
external DNS resolution to fail with the exact expected status and no output. Unpublished services are forbidden
from joining either non-internal access zone.

## Framework and secret evidence

`config.settings.testing` keeps debug mode off and is the production-shaped configuration for the deployment
checker. `manage.py check --deploy` must return only:

- `security.W004` — HSTS is inert on plaintext localhost.
- `security.W008` — redirecting to HTTPS would make the offline stack unreachable.
- `security.W012` — the session cookie cannot be secure-only on plaintext localhost.
- `security.W016` — the CSRF cookie cannot be secure-only on plaintext localhost.

The browser boundary still enforces content-type sniffing protection, frame denial, same-origin referrers, and the
project content security policy. Existing integration tests prove debug detail and tracebacks do not enter error
responses, authentication throttles fire, account existence is not disclosed by status/body/timing, Argon2 is the
active password hasher, token expiry/rotation/revocation works across HTTP and WebSocket authentication, and
unsigned object reads return 403.

Pinned Gitleaks 8.28.0 scans every revision reachable from any Git ref with explicit `--log-opts=--all` and
redaction. An independent `--staged` scan reads index blobs, and a third scan copies every current tracked and
untracked non-ignored project file into an isolated
snapshot, so staged, unstaged, deleted-after-staging, and not-yet-added work cannot escape the gate.
`.gitleaks.toml` contains only anchored literal documented fixture/protocol values. The current lock exports cleanly
and pinned `pip-audit` 2.10.1 reports no known Python dependency vulnerability.

Runtime checks derive sensitive variable names from every `<GENERATED>` manifest entry, including direct passwords
and composed credential-bearing URLs, without printing their values. They reject an exact occurrence in any
registered image history command or any retained required-container log, with no time-window truncation. Pinned
Trivy 0.68.2 additionally scans the actual filesystem layers of every registered image for secrets.

## Image vulnerability and secret disposition

Images were refreshed at their pinned tags. Locally built images pin base-image digests and every installed Debian
package version, and Compose disables mutable build provenance attestations so identical inputs retain one image
identity. Future dependency drift therefore fails the build instead of silently changing the runtime layer. The
current scan records the exact remaining high/critical findings in the reviewed snapshot; the runtime exposure gate
keeps internal services off public ports.

`environments-setup` rebuilds every local image through cached deterministic layers on each run rather than
trusting a pre-existing mutable tag. Consecutive no-edit builds reproduced the same three image IDs. The image
audit reports the exact tag, live identity, scanner, artifact, or policy field boundary when evidence drifts.
Trivy scans one target at a time and retries one non-zero scanner process result with the same cache and immutable
image identity. Standard output remains separate from standard error because the first audit after a complete Docker
cleanup auto-pulls the Trivy image: Docker writes pull progress to standard error while Trivy writes JSON to standard
output. Combining those streams corrupts valid JSON and caused the image audit to fail on its first Mailpit scan.
A second scanner failure remains fail-closed; malformed successful JSON is never retried or accepted.

The exact accepted snapshot is
[image-vulnerability-policy.json](./image-vulnerability-policy.json). It records every unique identifier, package
finding count, and a digest over target, identifier, package, installed version, fixed version, and severity.
It also records the immutable Docker image ID, Trivy artifact ID, and exact secret rule, category, path, and line
coordinates. Every required live container must run the immutable identity currently inspected for its registered
tag, and Trivy scans that exact live ID. Pinned external images must also retain the policy's reviewed image and
artifact identifiers. Locally built `localforge/*` images change identifiers whenever reviewed source or bundled
documentation changes, so their identifiers remain historical evidence while vulnerability and secret fields must
match exactly. A malformed or empty successful scanner response is rejected, and every image shares one isolated
temporary scan cache so the vulnerability database is fetched once per audit.

The refreshed live-image snapshot contains no secret findings. Recursive Docker ignore rules exclude generated
`__pycache__` and bytecode from every local image, and Trivy retains a matching defensive skip because the
corresponding source is scanned. Any future secret finding fails the policy unless it is explicitly reviewed and
documented with its exact path and line coordinates.

The following vulnerability findings remain only in current pinned upstream images and are accepted until the
upstream project publishes a replacement image; the live snapshot and review are due **2026-10-14**:

| Image | Fixable high/critical package findings | Reason pinned past |
| --- | ---: | --- |
| `chrislusf/seaweedfs:4.46` | 1 | current pinned upstream release; unsigned administration surfaces are not host-published |
| `cloudflare/cloudflared:2026.9.3` | 2 | current pinned upstream release; connector has no host-published port |
| `dpage/pgadmin4:9.17` | 6 | current pinned upstream release; authenticated UI is loopback-only |
| `ghcr.io/google/cadvisor:v0.60.5` | 25 | current pinned upstream release; no host publication |
| `grafana/alloy:v1.19.2` | 4 | current pinned upstream release; no host publication |
| `grafana/grafana-oss:13.0.2` | 84 | current pinned upstream release; authenticated UI is loopback-only |
| `grafana/loki:3.7.7` | 10 | current pinned upstream release; no host publication |
| `localforge/pgbackrest:18.6` | 29 | rebuilt from the current PostgreSQL 18.6 base; remaining records originate in that base |
| `postgres:18.6` | 29 | current pinned upstream release; database ports are loopback-only |
| `prom/prometheus:v3.14.0` | 6 | current pinned upstream release; no host publication |
| `quay.io/prometheuscommunity/postgres-exporter:v0.20.1` | 12 | current pinned upstream release; no host publication |

## Reliability evidence

Each development backing service was stopped independently: primary PostgreSQL, replica PostgreSQL, cache Valkey,
channel-layer Valkey, RabbitMQ, SeaweedFS, and Mailpit. In every window the deployed
`http://localforge.localhost:8080/health/` boundary returned 503 within the bounded poll and the complete
development health gate passed after restart. Dependency connection failures, deliberate 503 request records,
broker-forced closes, and database administrator shutdown records are expected only inside their matching outage
window. RabbitMQ recovery was repeated after the quorum change and produced no `global_qos`, delayed-delivery,
warning, error, or critical record.

Full-stack persistence was seeded before `development-down` and checked after `development-up`: an account row was
present on primary and replica, a no-expiry cache value survived, private S3 bytes were identical, Mailpit retained
the captured message, and a durable quorum-queue payload survived. All audit markers were then deleted.

The bounded restart window contained only documented startup records: transient SeaweedFS leader/socket formation,
Loki's pre-ring `empty ring`, pgAdmin's upstream Python 3.14 `SyntaxWarning`, and RabbitMQ's
`management_metrics_collection` deprecation warning. The prior Celery direct-exchange delayed-delivery warning and
RabbitMQ `global_qos` error were fixed rather than accepted.
