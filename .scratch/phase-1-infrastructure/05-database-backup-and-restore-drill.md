# 05: Automated database backup with verified restore

**What to build:** scheduled backups of the primary that a developer can prove are restorable. The backup agent
runs on its own schedule, retains a bounded history, and a documented drill restores a backup into a scratch
instance and finds the expected data.

**Blocked by:** 04.

**Status:** ready-for-agent

- [ ] The backup image is built from the same PostgreSQL base as the database, installing the backup tool from the
      vendor package repository already configured in that image.
- [ ] The image build runs an update before installing, because the base image ships no package lists.
- [ ] The primary archives its write-ahead log through the backup tool, and the database is restarted after the
      change so archiving is actually active.
- [ ] The stanza is created idempotently: an existing stanza is detected and not recreated.
- [ ] A configuration check passes, confirming the primary and the agent agree on the data directory.
- [ ] Full and differential backups run on separate schedules from the environment, and retention is bounded.
- [ ] The backup tool's own archive timeout is set deliberately rather than left at its default.
- [ ] Backup status is queryable and reports at least one successful full backup.
- [ ] A restore drill is documented and performed once: restore into a scratch instance and confirm a known row.
- [ ] Backup logs reach the log aggregation stack so a silent failure is visible.
