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

**Status:** done

- [x] Every account email is dispatched as a task; no request path sends mail synchronously.
- [x] Tasks receive only identifiers and the minimum context needed, never a password or a full user object.
      Credential-link tasks may carry the raw bearer because the database deliberately persists only its digest and
      the worker must render and revalidate that same bearer.
- [x] Retry-safe credential-free notification tasks retry with bounded backoff and stop after the configured
      maximum. Activation, password-reset, and username-reset bearer tasks preserve their completed at-most-once
      delivery contract: a claimed bearer is not retried after SMTP failure.
- [x] Tasks are idempotent: a redelivered task does not send a duplicate message where that would be harmful, or
      the duplicate is documented as acceptable.
- [x] Enqueueing happens after the database transaction commits, so a rolled-back registration never triggers a
      real email.
- [x] The API response does not change based on whether mail succeeded, so enumeration is not possible through
      timing or status.
- [x] In the testing environment tasks run eagerly and assert against the in-memory backend; every complete host
      and container gate temporarily starts Mailpit and runs all five real SMTP cases without skips.
- [x] Tests cover successful send, transient failure with retry and permanent failure for retry-safe notifications,
      plus the intentional no-retry failure outcome for credential-bearing delivery.
