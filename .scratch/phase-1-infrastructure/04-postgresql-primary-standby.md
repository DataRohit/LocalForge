# 04: PostgreSQL primary and streaming standby

**What to build:** a primary database and a read-only standby that is genuinely replicating from it. Starting the
stack produces two healthy nodes, both reachable from the host on separate ports, with the standby confirmed to be
in recovery and streaming.

**Blocked by:** 03.

**Status:** done

- [x] The primary starts with write-ahead logging configured for replication, a dedicated replication role, and a
      named physical replication slot.
- [x] Host-based authentication permits the replication connection and nothing wider.
- [x] The standby seeds itself from the primary on first start and skips seeding when its data directory is already
      a valid standby, so a restart is fast and non-destructive.
- [x] The standby uses the named slot, so the primary retains the write-ahead log it needs between the base backup
      and the start of streaming.
- [x] The primary's health check reports ready only when it accepts connections.
- [x] The standby's health check distinguishes a standby from a primary, not merely a live server.
- [x] Querying replication status on the primary shows exactly one streaming client.
- [x] Both nodes are reachable from the host on the ports in `docs/platform/service-inventory.md`.
- [x] Data written to the primary is readable from the standby.
- [x] Credentials come from the environment; none appears in a Compose file or an init script.
