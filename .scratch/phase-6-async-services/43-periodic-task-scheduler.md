# 43: Periodic task scheduler

**What to build:** a scheduler container that fires periodic work on time, with schedules editable through the
admin rather than hardcoded, and exactly one instance running so nothing double-fires.

**Blocked by:**

- [42](42-background-worker-service.md)

**Governing sources:**

- [Root instructions](../../AGENTS.md)
- [Build plan](../../docs/build/plan.md)
- [Celery and RabbitMQ ADR](../../docs/adr/0008-celery-rabbitmq.md)
- [Platform conventions](../../docs/platform/conventions.md)
- [Service inventory](../../docs/platform/service-inventory.md)
- [Kubernetes mapping](../../docs/platform/kubernetes-mapping.md)

**Status:** ready-for-agent

- [ ] The scheduler runs from the same image with the registry name and its own process, separate from the worker.
- [ ] Schedules are stored in the database and editable through the admin, so changing one needs no redeploy.
- [ ] Exactly one scheduler instance runs. Two would double-fire every periodic task, and the constraint is stated
      in the service's documentation and reflected in the Kubernetes mapping.
- [ ] The scheduler starts only after the database and broker are healthy, and it does not run migrations.
- [ ] At least one real periodic task is registered and observed to fire, proving the path end to end.
- [ ] The database-backed scheduler runs SimpleJWT's `flushexpiredtokens` management command once daily against the
      authoritative primary. An observed run removes expired outstanding tokens and their cascaded blacklist rows,
      preserves unexpired rows, and records a visible success or failure.
      [Ticket 30](../phase-4-rest-api/30-json-web-token-endpoints.md) proves the command semantics; this ticket owns
      operational scheduling and retention.
- [ ] The scheduler removes activation-token tombstones only after their signed maximum age has elapsed, in bounded
      primary-database batches ordered by issue time.
      [Ticket 32](../phase-4-rest-api/32-account-activation-and-resend.md) owns the nullable account reference,
      immutable subject, retention index, and classification semantics; this ticket owns operational scheduling and
      evidence.
- [ ] The same bounded primary cleanup removes password-reset tombstones only after their configured maximum age.
      [Ticket 33](../phase-4-rest-api/33-password-management-endpoints.md) owns their nullable account reference,
      immutable subject, issue-time index, and used/foreign classification; this ticket owns operational scheduling
      and evidence.
- [ ] The same bounded primary cleanup removes username-reset tombstones only after their configured maximum age.
      [Ticket 34](../phase-4-rest-api/34-username-management-endpoints.md) owns their nullable account reference,
      immutable subject, issue-time index, and used/foreign classification; this ticket owns operational scheduling
      and evidence.
- [ ] A periodic task that overruns its interval does not stack up unboundedly.
- [ ] Scheduler logs reach the log store, and a missed or failed run is visible.
- [ ] The schedule state persists across a restart without re-firing tasks that already ran.
