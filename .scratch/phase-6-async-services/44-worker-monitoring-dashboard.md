# 44: Worker monitoring dashboard

**What to build:** a browser UI showing live workers, queue depth, and task history, so a developer can see what
the queue is doing without reading logs.

**Blocked by:** 42.

**Status:** ready-for-agent

- [ ] The dashboard runs from the same built image with the registry name and is reachable from the host.
- [ ] Access requires basic authentication from a generated credential, supplied through the prefixed environment
      variable the tool reads.
- [ ] The unauthenticated API mode is not enabled.
- [ ] Registered workers, active tasks, queue lengths, and recent task outcomes are all visible.
- [ ] The dashboard starts only after the broker is healthy and does not block the worker if it is down.
- [ ] It is excluded from the testing environment, which runs headless.
- [ ] It sits on the application network only and is not exposed through the edge proxy without authentication.
