---
status: accepted
date: 2026-09-13
---

# Reject restricted-licence, archived, and phone-home tools

This platform accepts only tools that are OSI-licensed, actively maintained, and able to run with no network. That
constraint eliminated several defaults that a reader would otherwise expect to see, so the rejections are recorded
once here rather than argued repeatedly.

All evidence checked **2026-09-13**.

## Rejected, and why

| Tool | Ground | Detail |
|---|---|---|
| **MinIO** | archived | Repository **archived and read-only** with a "NO LONGER MAINTAINED" banner; the embedded Console was removed from the AGPL server in `RELEASE.2025-05-24T17-08-30Z`; `minio/console` and `minio/object-browser` now 404; `minio/mc` archived 2025-11-20; **`hub.docker.com/r/minio/minio` returns 404**; community docs redirect to the proprietary AIStor. See [0013](./0013-seaweedfs-object-storage.md) |
| **LocalStack** (community) | phone-home | GitHub repository archived; the current unified image **requires a `LOCALSTACK_AUTH_TOKEN` to start**, and offline activation needs re-activation every 24 hours. Incompatible with an offline platform |
| **Promtail** | EOL | **End of life 2026-03-02.** Official Loki docs state support has ended and all development moved to Alloy; `clients/cmd/promtail` no longer exists on `grafana/loki@main`; last image was `3.6.11` on 2026-05-13 while Loki is at 3.7.7. See [0010](./0010-loki-alloy-logging.md) |
| **Redis OSS 8.x** | licence | Tri-licensed RSALv2 / SSPLv1 / AGPLv3; only AGPLv3 is OSI-approved, none is permissive. Valkey is the BSD-3 continuation |
| **RedisInsight 3.8.0** | licence + capability | **SSPL v1**, not OSI-approved, *and* no Valkey support — "Valkey" appears zero times in its README and its fifteen most recent release notes |
| **Graylog Open 7.1.9** | licence | **SSPL-1.0**, and it additionally requires MongoDB and OpenSearch |
| **Elasticsearch / Kibana 9.5.3** | licence | Tri-licensed AGPL-3.0 / SSPL-1.0 / ELv2, and everything under `x-pack/` is **ELv2-only** — which the shipped `docker.elastic.co` images bundle |
| **HashiCorp Vault 2.1.0** | licence | **BUSL-1.1** since v1.15.0, with **IBM** now named as licensor. Not open source; carries a competitive-use restriction |
| **MailHog** | abandoned | No release since 2020-08-11, no commit since 2022-08-02 |
| **`prodrigestivill/postgres-backup-local`** | unmaintained | Zero tagged releases ever; image not rebuilt since 2025-09-26 |
| **direnv v2.37.1** | stalled | No release since 2025-07-20, no commits in thirteen weeks, 468 open issues. Compose `env_file` does the same job with no extra tool |

## Accepted copyleft

Rejecting restricted licences is not the same as rejecting copyleft. These are accepted as unmodified,
locally-run runtime dependencies, and are listed so the choice is never mistaken for permissive:

| Tool | Licence |
|---|---|
| Grafana OSS, Loki | AGPL-3.0 (relicensed from Apache-2.0 in 2021) |
| SOPS, Vector | MPL-2.0 |
| psycopg | LGPL-3.0-only |
| Dramatiq (if ever adopted) | LGPL-3.0 |
| Garage (if ever adopted) | AGPL-3.0 |

## Consequences

Two of these reverse widely-held defaults — MinIO for object storage and Promtail for log shipping. Both are called
out in `AGENTS.md` so an executing agent working from stale priors does not silently reintroduce them.
