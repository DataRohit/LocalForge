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

## The connection budget

Measured 2026-09-14. Both nodes allow **100** connections with three reserved for superusers, and the psycopg pool
Django creates is **per process, per alias** — `DatabaseWrapper._connection_pools` is a class attribute of the
backend, not a shared object. Sizing a per-process pool by the total worker count would therefore square it: at
four connections per worker, eight uvicorn workers would ask for 256 connections per node from the web tier alone.

So the pool takes its size from a fixed budget divided by the processes that will draw on it:

```text
max_size = max(minimum, web budget ÷ (uvicorn workers × 2 aliases))
```

The web budget is 32, which two workers spend as 2 × 2 × 8. Raising `UVICORN_WORKERS` shrinks each pool rather than
growing the total. Every process family added later — the worker, the scheduler, the dashboard — draws on the same
100, so each needs its own budget line here before it is added, and a Celery prefork worker multiplies by its
concurrency.

A read inside an atomic block is routed to the primary by `db_for_read`. Django does **not** do this itself:
`transaction.atomic()` passes no routing hint, so without it a block writes on one connection and reads on
another that cannot see the uncommitted rows. Measured the same day: sessions written at login were missed by the
following read roughly half the time, and one admin login in ten bounced back to the form.

## Testing

The `testing` environment runs a single PostgreSQL node with the `replica` alias pointing at it. Router code paths
stay exercised without doubling the infrastructure. **Replication lag behaviour is therefore not covered by the
suite** — an accepted, recorded limitation rather than an oversight.

The alias is declared `TEST = {"MIRROR": "default"}`, so the suite builds one test database rather than two. Two
consequences, measured 2026-09-14 when the router was wired:

- **A mirror is still its own connection.** A transaction-wrapped test writes on `default` and reads on `replica`,
  which cannot see the other connection's open transaction, so the read returns nothing. A test that writes and
  then reads back through the ORM therefore commits — `transaction=True` — which also makes it exercise the real
  two-connection path rather than a single-connection shortcut.
- **Every test touching the ORM declares both aliases.** Reads route to `replica`, so a test naming only `default`
  is refused by the test runner rather than silently served from the primary.

## Considered options

**Patroni** and **repmgr** — HA orchestration, automatic failover, and a consensus store. Meaningful in production,
pure overhead for a single-node local platform. Failover belongs to the Kubernetes phase, where an operator owns it;
see [../platform/kubernetes-mapping.md](../platform/kubernetes-mapping.md).

**pglogical / native logical replication** — replicates selected tables rather than the whole cluster. Right for
selective or cross-version replication, wrong for a replica that must be a faithful copy.

**Citus** — solves sharding, which is not the requirement.
