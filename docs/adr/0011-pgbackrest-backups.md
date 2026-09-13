---
status: accepted
date: 2026-09-13
---

# pgBackRest for database backup automation

Backups run through pgBackRest 2.59.1 (2026-08-17, MIT). It releases every two to three months, its repository was
pushed 2026-09-11, and it covers full, differential, and incremental backups, parallel compression, backup
verification, and point-in-time recovery from a WAL archive — the behaviours worth rehearsing locally before they
are ever needed for real.

## The cost: no first-party image

pgBackRest publishes **no official container image**, and the community ones are marginal (`woblerr/pgbackrest`, 6
stars). The platform builds its own. Four facts about the `postgres:18.6` base make this straightforward, all
verified 2026-09-13 against the image's Dockerfile:

1. The image is Debian **trixie**, and the **PGDG apt repository persists into the final image** — the build cleans
   up `temp.list` but leaves `pgdg.list`. The signing key is already at
   `/usr/local/share/keyrings/postgres.gpg.asc`.
2. Installing from PGDG therefore yields pgBackRest **2.59.1**, not trixie's older 2.55.1.
3. `/var/lib/apt/lists/*` is wiped, so the derived image must run `apt-get update` before `apt-get install
   pgbackrest`.
4. PGDG ships a binary `deb` line only on amd64, arm64, loong64, and ppc64el. On other architectures it is
   `deb-src` only and `apt-get install pgbackrest` will fail. This machine is x86_64, so it is unaffected.

The image build is the one step needing network access; every run afterwards is offline.

## Configuration shape

Verified against the official user guide, which is built against 2.59.1. Config is INI-like with `[global]` and
`[<stanza>]` sections:

```ini
[localforge]
pg1-path=/var/lib/postgresql/data

[global]
repo1-path=/var/lib/pgbackrest
repo1-retention-full=2
```

`pg1-path` must equal `data_directory` **exactly** as PostgreSQL reports it; a mismatch produces backup errors. On
the primary, `postgresql.conf` needs:

```ini
archive_mode = on
archive_command = 'pgbackrest --stanza=localforge archive-push %p'
```

PostgreSQL must be restarted after this and before the first backup. Commands are `stanza-create`, `check`,
`backup --type=full`, and `info`.

One trap: pgBackRest's own `archive-timeout` defaults to **60 seconds** and is *not* PostgreSQL's `archive_timeout`.
Raise it if a WAL segment takes longer than a minute to reach the repository.

## Considered options

**Barman 3.20.0** — equally capable and actively maintained by EDB, but GPL-3.0, also without an official image, and
built around a dedicated backup host rather than a sidecar container.

**wal-g v3.0.9** — fast and active, but oriented toward cloud object storage; its local-filesystem path is the
least-exercised one, and it ships release binaries only, with 317 open issues.

**`prodrigestivill/postgres-backup-local`** — the convenience option, and the one with an image. Disqualified on
maintenance: zero tagged releases ever, an image not rebuilt since **2025-09-26**, and only documentation commits
since. See [0015](./0015-reject-restricted-licenses.md).
