# 19: Database integration with read-replica routing

**What to build:** Django reading from the standby and writing to the primary, automatically, with migrations
always going to the primary. A developer can see in the query log which connection served which statement.

**Blocked by:** 04, 17, 18.

**Status:** done

- [x] Two database aliases are configured from the environment, one for the primary and one for the replica.
- [x] A router sends reads to the replica and sends writes, migrations, and anything inside an atomic block to the
      primary.
- [x] The router permits migration only on the primary alias. Pointing a migration at the read-only standby would
      fail, and this is the project's own rule rather than a framework default, so it is stated in the router's
      docstring.
- [x] Router ordering in settings is deliberate and documented, because a catch-all router placed first would make
      every model available on every alias.
- [x] Connection health checks and a bounded connection lifetime are configured, so a dropped connection is
      replaced rather than reused.
- [x] Connection pooling settings are tuned for the worker count rather than left at defaults.
- [x] The testing environment points the replica alias at its single node, so router paths are still exercised.
- [x] Integration tests assert that a read uses the replica alias and a write uses the primary alias.
- [x] A test documents the known limitation that replication lag is not covered, and the limitation is recorded in
      the relevant decision record.
- [x] Migrations apply cleanly and the migration check reports nothing outstanding.
