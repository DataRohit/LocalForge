# Restore drill

Authoritative for: proving a backup is restorable, and the four things that make a first attempt fail.

Performed successfully on **2026-09-14** against `pgbackrest-pb2wj` 2.59.1 and `postgres:18.6`. Re-run it whenever
the backup configuration changes; a backup nobody has restored is a hypothesis, not a backup.

## 1. Write a row you can recognise

```console
docker exec postgres-pg3ka psql -U localforge_app -d localforge -qc "CREATE TABLE IF NOT EXISTS restore_drill(id int primary key, note text); INSERT INTO restore_drill VALUES (1,'survives-a-restore') ON CONFLICT DO NOTHING;"
docker exec -u postgres pgbackrest-pb2wj pgbackrest --stanza=localforge --type=full backup
docker exec postgres-pg3ka psql -U localforge_app -d localforge -tAc "SELECT pg_switch_wal() IS NOT NULL;"
```

## 2. Confirm the repository holds it

```console
docker exec -u postgres pgbackrest-pb2wj pgbackrest --stanza=localforge info
```

Pass: `status: ok`, at least one `full backup`, and a `wal archive min/max` range.

## 3. Restore into a scratch volume

```console
docker volume create pgbackrest-drill-scratch
docker run --rm -u root -v pgbackrest-drill-scratch:/scratch --entrypoint sh localforge/pgbackrest:18.6 -c "install -d -o postgres -g postgres -m 0700 /scratch/data"
docker run --rm -u postgres -v pgbackrest-pb2wj-repo:/var/lib/pgbackrest -v pgbackrest-drill-scratch:/scratch --entrypoint sh localforge/pgbackrest:18.6 -c "pgbackrest --stanza=localforge --pg1-path=/scratch/data --type=immediate --target-action=promote restore"
```

## 4. Start it and read the row back

```console
docker run -u postgres --name pgdrill -d -v pgbackrest-drill-scratch:/scratch -v pgbackrest-pb2wj-repo:/var/lib/pgbackrest -v "$PWD/docker/postgres/standby/conf.d:/etc/postgresql/conf.d:ro" --entrypoint sh localforge/pgbackrest:18.6 -c "pg_ctl -D /scratch/data -o '-c listen_addresses=localhost -c archive_mode=off -c unix_socket_directories=/tmp' -w start; sleep 600"
docker exec -u postgres pgdrill psql -h /tmp -U localforge_app -d localforge -tAc "SELECT note FROM restore_drill WHERE id=1;"
```

Pass: `survives-a-restore`.

## 5. Clean up

```console
docker rm -f pgdrill
docker volume rm pgbackrest-drill-scratch
```

## 6. Four failures this drill actually hit

Each cost a cycle the first time and is invisible until the instance refuses to start.

| Symptom | Cause | Remedy |
|---|---|---|
| `unable to create path '/scratch/data': [13] Permission denied` | A fresh named volume is `root:root`; pgBackRest restores as `postgres` | Create the target with `install -d -o postgres -g postgres -m 0700` first, as step 3 does |
| `could not open configuration directory "/etc/postgresql/conf.d"` then `configuration file ... contains errors` | The backup carries the primary's `postgresql.conf`, which carries its `include_dir` | Mount a configuration directory at that path, as step 4 does |
| Startup aborts pointing at `backup_label` and `recovery.signal` | The restored cluster replays WAL through `restore_command`, which shells out to `pgbackrest` | Mount `pgbackrest-pb2wj-repo` into the restored instance as well, as step 4 does |
| `FATAL: role "postgres" does not exist` | pgBackRest defaults to the `postgres` role and database; this platform uses `localforge_app` and `localforge` | Already set as `pg1-user` and `pg1-database` in `docker/pgbackrest/pgbackrest.conf` |

## 7. What the drill does not prove

Point-in-time recovery to an arbitrary timestamp, and recovery from a repository that has lost a WAL segment.
Both are exercises for the phase 6 gate rather than this drill, which proves only that a full backup plus its
archived WAL reconstitutes a cluster containing a known row.
