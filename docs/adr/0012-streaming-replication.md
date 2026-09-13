---
status: accepted
date: 2026-09-13
---

# PostgreSQL native streaming replication for the read replica

The read replica is a physical hot standby fed by PostgreSQL's own streaming replication — one primary, one
standby, a physical replication slot, and a Django database router. No external replication tooling.

All mechanics below verified 2026-09-13 against the PostgreSQL 18 documentation.

## Mechanics

On the primary, `wal_level = replica` is **already the default** in PostgreSQL 18; `minimal` would prevent the
server from even starting when `max_wal_senders` is non-zero. Also required: a role with `REPLICATION`, a
`pg_hba.conf` entry whose database field is the literal `replication`, and `max_replication_slots` high enough for
the slot.

The standby is seeded with `pg_basebackup`. Three details that are easy to get wrong:

- **`-R` does not write `recovery.conf`.** That file is gone. `-R` creates **`standby.signal`** and appends the
  connection settings to **`postgresql.auto.conf`**. A server enters standby mode because `standby.signal` exists.
- **`-X stream` is the default**, not an opt-in. Keeping it explicit is self-documenting and harmless. What *is*
  load-bearing is not passing `--target`, which is incompatible with it.
- **`--slot` requires `-X stream`**, and the slot must already exist unless `-C` is also passed. The standby must
  then use the same name as its `primary_slot_name`, which is what stops the primary discarding WAL between the end
  of the base backup and the start of streaming.

Verification is `SELECT pg_is_in_recovery()` returning `t` on the standby, and one row in `pg_stat_replication` on
the primary with `state = streaming`.

## Django wiring, and a gotcha that is ours, not Django's

Two `DATABASES` aliases, `default` and `replica`, with a router sending reads to `replica` and writes, migrations,
and transactional blocks to `default`.

`allow_migrate` must return `True` only for `default`. **This is not a documented Django rule** — it is a
consequence of our topology, and the distinction matters because Django's own `PrimaryReplicaRouter` example returns
`True` unconditionally. That example's "replicas" are separate migrated databases, not physical read-only standbys.
Point a migration at a real standby and the DDL fails against a read-only server. Handling that is ours.

The gotchas Django *does* document, and which apply here:

- If `allow_migrate()` returns `False`, migration operations are **silently skipped** — and changing its behaviour
  for models that already have migrations can leave broken foreign keys, extra tables, or missing tables.
- `makemigrations` is the exception to the one-database-at-a-time rule and consults `allow_migrate` across routers.
- Router **order** in `DATABASE_ROUTERS` decides the outcome; a catch-all router placed first makes every model
  available on every database.
- The documented primary/replica caveat is about **replication lag**, not migrations: Django offers no built-in
  answer for a read that arrives before the write has replicated.

## Testing

The `testing` environment runs a single PostgreSQL node with the `replica` alias pointing at it. Router code paths
stay exercised without doubling the infrastructure. **Replication lag behaviour is therefore not covered by the
suite** — an accepted, recorded limitation rather than an oversight.

## Considered options

**Patroni** and **repmgr** — HA orchestration, automatic failover, and a consensus store. Meaningful in production,
pure overhead for a single-node local platform. Failover belongs to the Kubernetes phase, where an operator owns it;
see [../platform/kubernetes-mapping.md](../platform/kubernetes-mapping.md).

**pglogical / native logical replication** — replicates selected tables rather than the whole cluster. Right for
selective or cross-version replication, wrong for a replica that must be a faithful copy.

**Citus** — solves sharding, which is not the requirement.
