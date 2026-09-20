# 05: Automated database backup with verified restore

**What to build:** scheduled backups of the primary that a developer can prove are restorable. The backup agent
runs on its own schedule, retains a bounded history, and a documented drill restores a backup into a scratch
instance and finds the expected data.

**Blocked by:**

- [04](04-postgresql-primary-standby.md)

**Governing sources:**

- [Root instructions](../../AGENTS.md)
- [Build prerequisites](../../docs/build/prerequisites.md)
- [Build plan](../../docs/build/plan.md)
- [Platform conventions](../../docs/platform/conventions.md)
- [Service inventory](../../docs/platform/service-inventory.md)
- [Architecture decision index](../../docs/adr/README.md)

**Status:** done

- [x] The backup image is built from the same PostgreSQL base as the database, installing the backup tool from the
      vendor package repository already configured in that image.
- [x] The image build runs an update before installing, because the base image ships no package lists.
- [x] The primary archives its write-ahead log through the backup tool, and the database is restarted after the
      change so archiving is actually active.
- [x] The stanza is created idempotently: an existing stanza is detected and not recreated.
- [x] A configuration check passes, confirming the primary and the agent agree on the data directory.
- [x] Full and differential backups run on separate schedules from the environment, and retention is bounded.
- [x] The backup tool's own archive timeout is set deliberately rather than left at its default.
- [x] Backup status is queryable and reports at least one successful full backup.
- [x] A restore drill is documented and performed once: restore into a scratch instance and confirm a known row.
- [x] Backup logs are written to the container's standard streams so the log collector can pick them up without
      further configuration. The end-to-end assertion that a backup line is retrievable from log storage belongs to
      [ticket 11](11-metrics-and-log-aggregation.md), which introduces the collector and the store; this ticket
      cannot assert it because neither exists.
