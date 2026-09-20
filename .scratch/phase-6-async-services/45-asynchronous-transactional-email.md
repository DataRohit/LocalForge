# 45: Asynchronous transactional email

**What to build:** account emails — activation, password reset, username reset — sent by the worker rather than
inside the request, so a slow mail server never delays a response, and a transient failure retries instead of
losing the message.

**Blocked by:**

- [42](42-background-worker-service.md)
- [32](../phase-4-rest-api/32-account-activation-and-resend.md)
- [33](../phase-4-rest-api/33-password-management-endpoints.md)
- [34](../phase-4-rest-api/34-username-management-endpoints.md)

**Governing sources:**

- [Root instructions](../../AGENTS.md)
- [Build plan](../../docs/build/plan.md)
- [Celery and RabbitMQ ADR](../../docs/adr/0008-celery-rabbitmq.md)
- [Platform conventions](../../docs/platform/conventions.md)
- [Service inventory](../../docs/platform/service-inventory.md)
- [Kubernetes mapping](../../docs/platform/kubernetes-mapping.md)

**Status:** ready-for-agent

- [ ] Every account email is dispatched as a task; no request path sends mail synchronously.
- [ ] Tasks receive only identifiers and the minimum context needed, never a password, a raw token, or a full user
      object.
- [ ] A task that fails to send retries with bounded backoff and stops after the configured maximum.
- [ ] Tasks are idempotent: a redelivered task does not send a duplicate message where that would be harmful, or
      the duplicate is documented as acceptable.
- [ ] Enqueueing happens after the database transaction commits, so a rolled-back registration never triggers a
      real email.
- [ ] The API response does not change based on whether mail succeeded, so enumeration is not possible through
      timing or status.
- [ ] In the testing environment tasks run eagerly and assert against the in-memory backend; the SMTP path is
      covered by one profile-gated integration test.
- [ ] Tests cover successful send, transient failure with retry, and permanent failure.
