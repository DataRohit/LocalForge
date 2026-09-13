# 42: Background worker service

**What to build:** a worker container consuming from the broker and executing tasks, observable in metrics and
logs, and safe to stop and start without losing work.

**Blocked by:** 22.

**Status:** ready-for-agent

- [ ] The worker runs from the same built image as the application, with the registry name, and does not run
      migrations.
- [ ] Concurrency and prefetch are set from the environment rather than left at defaults that assume a large host.
- [ ] The worker starts only after the broker and result backend are healthy.
- [ ] Queues are declared explicitly, with a default queue and at least one separate queue for slow work, so a long
      task cannot starve fast ones.
- [ ] Graceful shutdown is configured: on stop, the worker finishes in-flight tasks within a bounded window before
      exiting.
- [ ] A worker health check reports unhealthy when the worker cannot reach the broker.
- [ ] Worker logs reach the log store with the task name and identifier.
- [ ] A task that raises is retried per the configured policy and lands in a dead-letter state after the bound,
      rather than retrying forever.
- [ ] An integration test enqueues work against the real broker and asserts the worker executed it.
- [ ] Restarting the worker mid-task redelivers rather than loses the task.
