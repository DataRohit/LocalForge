# 44: Worker monitoring dashboard

**What to build:** a browser UI showing live workers, queue depth, and task history, so a developer can see what
the queue is doing without reading logs.

**Blocked by:**

- [42](42-background-worker-service.md)

**Governing sources:**

- [Root instructions](../../AGENTS.md)
- [Build plan](../../docs/build/plan.md)
- [Celery and RabbitMQ ADR](../../docs/adr/0008-celery-rabbitmq.md)
- [Platform conventions](../../docs/platform/conventions.md)
- [Service inventory](../../docs/platform/service-inventory.md)
- [Kubernetes mapping](../../docs/platform/kubernetes-mapping.md)

**Status:** done

- [x] The dashboard runs from the same built image with the registry name and is reachable from the host.
- [x] Access requires basic authentication from a generated credential, supplied through the prefixed environment
      variable the tool reads.
- [x] The unauthenticated API mode is not enabled.
- [x] Registered workers, active tasks, queue lengths, and recent task outcomes are all visible.
- [x] The dashboard starts only after the broker is healthy and does not block the worker if it is down.
- [x] It is excluded from the testing environment, which runs headless.
- [x] It sits on the application network only and is not exposed through the edge proxy without authentication.
