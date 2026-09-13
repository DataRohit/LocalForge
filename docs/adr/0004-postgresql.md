---
status: accepted
date: 2026-09-13
---

# PostgreSQL 18 as the relational database

PostgreSQL 18.6 (2026-08-13) is the database. Major 18 is current and supported until **2030-11-14**, minors arrive
on a fixed quarterly schedule (February, May, August, November) plus out-of-cycle security releases, and the
`postgres:18.6` official image was pushed 2026-08-26. The license is the PostgreSQL License, unchanged.

Do not start this stack on PostgreSQL 14 — it reaches EOL on **2026-11-12**.

The driver is `psycopg` 3.3.5 (2026-08-31, Python 3.14 classifier). Its license is **LGPL-3.0-only**, which is fine
for an unmodified runtime dependency but is recorded because the rest of the Python stack is BSD/MIT/Apache.

## Considered options

**SQLite** — what the repository uses today. It cannot satisfy the read-replication requirement in
[0012](./0012-streaming-replication.md), serializes writers under an ASGI worker pool, and differs enough in
behaviour that a passing test suite would prove little about the real target.

**MySQL / MariaDB** — no native equivalent of the PostgreSQL features Django exposes (`ArrayField`, JSONB
containment, `unaccent`, rich `CheckConstraint` support), and a replication story that is harder to reason about in
a single-node local setup.
