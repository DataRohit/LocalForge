---
status: accepted
date: 2026-09-13
---

# Loki with Grafana Alloy for centralized logging

Container logs are collected by Grafana Alloy v1.19.2 (2026-08-26, Apache-2.0), stored and queried by Grafana Loki
3.7.7 (2026-08-27, AGPL-3.0), and read through the Grafana instance from [0009](./0009-prometheus-grafana.md). Loki
ships no UI of its own; Grafana Explore is it.

**Promtail is not an option.** It reached end of life on **2026-03-02** — see
[0015](./0015-reject-restricted-licenses.md) for the evidence. Alloy is its named replacement and ships an official
conversion tool.

## Considered options

**Vector v0.58.0** (MPL-2.0) and **Fluent Bit v5.1.2** (Apache-2.0) — both excellent and actively maintained. They
lost only on integration: Alloy is built by the same project as Loki and is Apache-2.0 despite Loki being AGPL-3.0.
Vector is the fallback if Alloy's resource usage proves excessive on a laptop.

**OpenSearch + Dashboards 3.8.0** — clean Apache-2.0 licence and the right answer if Elastic-style search were a
requirement. Rejected on weight: a JVM search cluster plus a separate dashboards service costs several gigabytes of
RAM to satisfy a need that is really "query my container logs by label".

**Elasticsearch + Kibana** and **Graylog** — rejected on licence, see
[0015](./0015-reject-restricted-licenses.md).

## Consequences

Alloy reads the Docker socket to discover container logs, mounted read-only. It and Traefik are the only services
permitted to see it. In Kubernetes Alloy becomes a DaemonSet reading node log paths — the one workload in this
platform whose replica count is a function of cluster size.
