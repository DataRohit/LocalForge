# 42: Background worker service

**What to build:** a worker container consuming from the broker and executing tasks, observable in metrics and
logs, and safe to stop and start without losing work.

**Blocked by:**

- [22](../phase-3-infrastructure-integration/22-task-queue-integration.md)

**Governing sources:**

- [Root instructions](../../AGENTS.md)
- [Build plan](../../docs/build/plan.md)
- [Celery and RabbitMQ ADR](../../docs/adr/0008-celery-rabbitmq.md)
- [Platform conventions](../../docs/platform/conventions.md)
- [Service inventory](../../docs/platform/service-inventory.md)
- [Kubernetes mapping](../../docs/platform/kubernetes-mapping.md)

**Status:** done

- [x] The worker runs from the same built image as the application, with the registry name, and does not run
      migrations.
- [x] Concurrency and prefetch are set from the environment rather than left at defaults that assume a large host.
- [x] The worker starts only after the broker and result backend are healthy.
- [x] Queues are declared explicitly, with a default queue and at least one separate queue for slow work, so a long
      task cannot starve fast ones.
- [x] Graceful shutdown is configured: on stop, the worker finishes in-flight tasks within a bounded window before
      exiting.
- [x] A worker health check reports unhealthy when the worker cannot reach the broker.
- [x] Worker logs reach the log store with the task name and identifier.
- [x] A task that raises is retried per the configured policy and lands in a dead-letter state after the bound,
      rather than retrying forever.
- [x] An integration test enqueues work against the real broker and asserts the worker executed it.
- [x] Restarting the worker mid-task redelivers rather than loses the task.
