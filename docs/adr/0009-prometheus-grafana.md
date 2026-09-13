---
status: accepted
date: 2026-09-13
---

# Prometheus and Grafana for monitoring

Metrics are collected by Prometheus v3.14.0 (2026-08-18) and visualized in Grafana OSS 13.2.1 (2026-09-02).
Prometheus ships roughly six-week minors plus patches and maintains an LTS line (v3.5.5); Grafana ships monthly
minors and patches four release lines in parallel. Both repositories were pushed within two days of this decision.

Grafana OSS is **AGPL-3.0**, relicensed from Apache-2.0 in 2021. For an unmodified, locally-run development platform
that imposes no distribution obligation, but it is recorded so the choice is not mistaken for permissive.

## Instrumentation

| Component | Version | Scrapes |
|---|---|---|
| `django-prometheus` 2.5.0 | 2026-05-26 | the Django process |
| `postgres_exporter` v0.20.1 | 2026-07-08 | PostgreSQL primary and replica |
| `redis_exporter` v1.91.1 | 2026-09-07 | both Valkey instances |
| cAdvisor v0.60.5 | 2026-07-11 | per-container CPU, memory, IO |

`django-prometheus` declares Django 6.0 and Python 3.14 and has moved to community stewardship under
**`django-commons/django-prometheus`**. Use that path, not the old `korfuri/` one.

## Grafana is provisioned as code

Datasource and dashboard provider files are mounted read-only from `docker/grafana/provisioning/`, so wiping the
Grafana volume loses nothing that matters. This also makes the Kubernetes move a `ConfigMap` rather than a
re-clicking exercise.

## Consequences

**cAdvisor is in low-volume maintenance mode.** Verified 2026-09-13: 119 commits in 52 weeks, one in the last four,
releases arriving in bursts after a five-month gap — but not archived, with a named active core team and a low open
issue count that reflects narrow scope rather than neglect. It is accepted with a defined substitution trigger: if
cAdvisor is archived, or has had no release for twelve months when the platform is next touched, replace it with the
OpenTelemetry Collector's Docker receiver (v0.160.0, on a two-week release train). In Kubernetes the question
disappears — kubelet already exposes cAdvisor metrics.

**Valkey has no native UI and its obvious companion is unusable** — RedisInsight is SSPL and has no Valkey support.
The Valkey dashboard is therefore `redis_exporter` rendered in Grafana. See
[0005](./0005-valkey-cache.md) and [0015](./0015-reject-restricted-licenses.md).
