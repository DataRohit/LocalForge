---
status: accepted
date: 2026-09-13
---

# SeaweedFS as the object storage emulator

File uploads go to SeaweedFS 4.46 (2026-09-08, Apache-2.0) through its S3 gateway. Multiple releases a month,
repository pushed 2026-09-13, 34.6k stars, and it ships both a master status UI and a filer browser UI — so the
dashboard requirement is met natively rather than through a companion.

**This choice reverses the industry default.** MinIO is archived, its Docker Hub image is gone, and its Console was
removed from the AGPL server; LocalStack's community edition is archived and its current image demands an auth
token. The full evidence is in [0015](./0015-reject-restricted-licenses.md). An executing agent working from
pre-2026 priors will reach for MinIO — it is not available.

## Running it

`weed server -s3` starts master, volume, filer, and S3 gateway in one process. `-s3` **implicitly enables the
filer**, so `-filer` is redundant.

Verified against the 4.46 source on 2026-09-13, that command opens more ports than the obvious four:

| Port | Service | Note |
|---|---|---|
| 9333 | master HTTP | UI at `/` and `/ui/index.html` |
| 8080 | volume HTTP | |
| 8888 | filer HTTP | browser UI |
| 8333 | S3 HTTP | the endpoint Django talks to |
| 19333 / 18080 / 18888 / 18333 | gRPC | derived as `10000 + port` |
| **8181** | **Iceberg REST catalog** | on by default in 4.46 |
| **9101** | **Lance namespace server** | on by default in 4.46 |

Pass `-s3.port.iceberg=0 -s3.port.lance=0` to stop the last two binding. 9101 is also the conventional
`node_exporter` port and 8181 is a common default elsewhere, so leaving them on invites a collision for two services
this platform does not use.

Two further flags were measured as necessary on 2026-09-14, when the service was built:

- `-ip.bind=0.0.0.0`. `-ip.bind` defaults to `-ip`, which defaults to the first container address detected. On a
  container joined to both a service zone and an access zone that is whichever Docker enumerated first, so a
  published port accepts the connection and immediately closes it while the container stays healthy.
- `-master.telemetry=false`. `weed server` otherwise reports to `https://telemetry.seaweedfs.com/api/collect`, and
  this service sits on a non-internal access zone. [0015](./0015-reject-restricted-licenses.md) rejects phone-home
  tools on principle; SeaweedFS is kept because the behaviour is a default that can be turned off, not a condition
  of use. Under `weed server` the flag is namespaced to the master, so the bare `-telemetry=false` the log line
  suggests is not accepted by that subcommand.

Health endpoints differ by component, and the naive guess is wrong:

- **S3 gateway**: `/status`, `/healthz`, and `/readyz` all work and share one handler.
- **Master**: there is **no `/status` route**. Use `/healthz` or `/readyz`. (`/dir/status` and `/vol/status` exist
  but are guarded by an IP allowlist.)

Using `/healthz` uniformly across both avoids the trap.

## Considered options

**Garage v2.4.1** — genuinely well maintained with a clean S3 implementation. Rejected on two counts: it ships **no
web UI**, so it would need a companion dashboard that does not exist, and it is AGPL-3.0. It publishes no support or
EOL policy, only an upgrade policy forbidding skipped majors.

**`weed mini`** — a purpose-built single-node all-in-one added in 4.x that is simpler than `server -s3` and also
runs the Admin UI. A reasonable future simplification; `server -s3` is kept for now because its flags map one-to-one
onto the components that become separate Kubernetes workloads later.

## Consequences

Django reaches SeaweedFS only through `S3_ENDPOINT_URL`, so swapping the implementation — to `weed mini`, to Garage,
or to a real S3 — is a variable change. The client-library question is separate and is settled in
[0016](./0016-accept-release-lag.md).
