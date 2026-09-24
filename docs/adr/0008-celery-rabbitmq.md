---
status: accepted
date: 2026-09-13
---

# Celery with RabbitMQ for background tasks and scheduling

Background work runs on Celery 5.6.3 with `django-celery-beat` 2.9.0 as the scheduler and RabbitMQ 4.3.5 as the
broker. Celery shipped eight releases in twelve months and its repository was pushed 2026-09-13.

Celery's published artifact carries release lag against Python 3.14 — its CI matrix on `main` tests 3.14 on Ubuntu
and Windows while the shipped 5.6.3 classifiers stop at 3.13. This was the platform's largest open question and it
is now closed in [0016](./0016-accept-release-lag.md). It is not re-argued here.

## Broker: RabbitMQ, not Valkey

Celery's documentation treats RabbitMQ as the reference broker with the strongest delivery guarantees, and using
Valkey would couple task delivery to the same process serving cache eviction.

**RabbitMQ 4.3.x reaches End of Community Support on 2026-11-30**, roughly 2.5 months after this decision.
Commercial support runs to 2028-04-30 and is irrelevant here. The executing agent checks for a newer
community-supported series before pinning, and if none exists, pins `4.3.5-management` and records a review date of
2026-11-30. This is a scheduled maintenance obligation, not an unknown.

**Checked 2026-09-14, on building the broker.** The upstream site's own release table data
(`docusaurus.config.js`, `customFields.releaseBranches.rabbitmq`) lists 4.3 as the newest series, with no
`current` releases published behind it, `end_of_community_support: "2026-11-30"`, and 4.3.5 (2026-08-17) as its
latest patch. No newer community-supported series exists, so `4.3.5-management` is pinned as this decision
prescribed. **Review on 2026-11-30**, at which point the series leaves community support and a newer one must be
adopted.

A newly created RabbitMQ data volume emits two bounded first-boot warnings before application clients connect:
classic peer discovery starts from an empty local-node list, and the empty persistent message store rebuilds its
indices from scratch. Both occur once while the broker initializes, before Compose marks it healthy. They are
expected only in the fresh-start observation window; recurrence after readiness or during ordinary restart is a
failure.

## Result backend

Task results go to the Valkey cache instance. `django-celery-results` 2.6.0 is **rejected**: zero releases in twelve
months and Django classifiers stopping at 5.2. Routing results to Valkey removes a stale dependency and keeps a
write path out of PostgreSQL.

Reviewed 2026-09-23 after warnings became test failures. Celery 5.6.3 calls redis-py's deprecated `setex` method for
every expiring result. `LocalForgeRedisBackend` preserves the same expiry and publication semantics through
`SET ... EX`, and its worker cleanup closes the result consumer plus connection pool so focused thread workers and
production shutdown do not abandon Redis sockets.

## Quorum queues keep QoS current

Reviewed 2026-09-22 during the degraded-recovery audit. Celery's classic-queue compatibility path requests
RabbitMQ's deprecated global QoS mode during worker reconnect. RabbitMQ 4.3 records that successful recovery as an
error even though the worker falls back and becomes ready, violating the platform rule that success cannot be
represented at failure level.

The consumed default and slow queues are durable quorum queues, `worker_detect_quorum_queues` is enabled, and their
exchanges are topic exchanges so Celery's native delayed-delivery topology can bind without warning. The terminal
dead-letter queue is not a worker-consumed queue; it is declared as a durable quorum queue with a topic exchange
only when a terminal record is published. Broker transport options require `confirm_publish`, so a producer does
not report success until RabbitMQ confirms durable acceptance. A stopped-broker publication fails, and publication
plus retrieval succeeds after recovery. After a broker outage the worker reconnects without `global_qos`,
delayed-delivery, warning, error, or critical records, and its bounded round-trip health probe passes.

Kombu creates each native delayed-delivery queue before declaring the next exchange named by that queue's
dead-letter configuration. A fresh RabbitMQ therefore records 28 missing-exchange warnings even though Kombu
declares the exchanges immediately afterwards and the topology succeeds. `scripts/prepare_broker.py` idempotently
declares all 29 durable topic exchanges after dependency readiness but before Django, any Celery companion, or the
persistent test runner becomes healthy. The fresh-clone dual-mode gate therefore starts from an empty broker with
zero delayed-delivery warning records instead of accepting successful setup logged as failure.

The quorum/topic topology uses versioned names: `localforge.v2.default`, `localforge.v2.slow`, and
`localforge.v2.dead-letter`. These registry identities are application and Compose constants rather than generated
environment values, so an in-place upgrade ignores any obsolete queue entries preserved in an older local env file.
Worker startup never deletes the legacy unversioned topology. Versioned names avoid the incompatible classic/direct
redeclaration while leaving any old messages and bindings intact. An operator may remove legacy resources only in
an explicit maintenance window after stopping every Django, worker, and scheduler process that can publish,
positively verifying the queues are empty and unused, and retaining the previous worker image until any remaining
work is drained. Automatic queue-then-exchange deletion is rejected because a publisher can race between those
operations and receive acceptance for an unroutable task.

Results use **logical database 1**, not the cache's 0. A `FLUSHDB` ignores the key prefix and erases the whole
logical database, so sharing an index would mean a routine cache flush destroying every pending task result.

## A worker needs exclusive queues on this broker

Measured 2026-09-14, on integrating the queue. RabbitMQ 4.3.5 has deprecated `transient_nonexcl_queues` and
**refuses them by default** — `rabbitmqctl list_deprecated_features` reports it `denied_by_default`. A Celery worker
and its clients declare that shape in three places, governed by two settings, and are refused at whichever they
reach first:

| Declared by | Queue | Setting that fixes it |
| --- | --- | --- |
| Control | the per-worker pidbox mailbox | `control_queue_exclusive` |
| Mingle, and every control client | one reply queue per client, keyed by its object id | `control_queue_exclusive` |
| Gossip | the worker event queue | `event_queue_exclusive` |

Neither failure is clean. The broker closes the connection with `INTERNAL_ERROR (541)`, Celery reads that as
connection loss and reconnects, and the worker spins without ever reaching `ready` — measured with `--pool=solo`,
36 refusals in 45 seconds, each traceback naming `transient_nonexcl_queues`. The count is pool-dependent, so it is
the shape that matters, not the number: the cause is visible in the log, but what is not visible is that fixing the
first declaration only moves the failure to the next.

Both are set to `True`. An exclusive queue is bound to the connection that declared it and dies with it, which is
what a per-worker mailbox and a per-worker event queue both want anyway, and it is not the deprecated shape.
Measured after the change: a real `celery -A config worker` reaches `ready` with **zero** refusals, and
`app.control.ping()` from a separate process returns `pong`. Removing `event_queue_exclusive` alone puts it back to
never reaching `ready`.

Two alternatives were rejected. Permitting the deprecated feature in the broker's configuration buys the same
outage later, since it is scheduled for removal outright. `control_queue_durable` is worse than useless here: Celery
refuses it together with `control_queue_exclusive`, and on its own it leaves a durable mailbox behind for every
worker that has ever run.

Turning remote control off instead is **not** available: [../build/plan.md](../build/plan.md) gate 6d proves the
worker with `celery -A config inspect ping`, which travels over the mailbox, and Flower reads the event queue.

**The in-process test worker cannot prove this.** `celery.contrib.testing.worker.start_worker` hardcodes
`without_gossip=True` and `without_mingle=True`, so while it does exercise the control mailbox, it never declares
Mingle's reply queue or Gossip's event queue. The suite therefore declares each of the three against the real
broker directly and asserts it is exclusive, rather than inferring health from a worker that skips two of them.

## The queue does not own the log stream

`worker_hijack_root_logger` defaults to **true**, which replaces the root logger's handlers when a worker starts.
This platform configures one structured handler for every process, so a hijacking worker would emit prose where the
collector in [0010](./0010-loki-alloy-logging.md) expects JSON. It is set to false.

Task arguments are a second log hazard, and they need two seams because they escape by two routes. The worker logs
a received and a failed task from the message's `argsrepr` and `kwargsrepr` fields, independently of anything the
task class does, so the redaction is attached to `before_task_publish` rather than to the task class: `send_task`
never goes through `Task.apply_async`, so a publisher that does not hold the task object — `django-health-check`, or
any third-party caller — would otherwise escape it. Positional arguments are reduced to their type names, since a
positional argument has no name to judge it by, and keyword arguments keep their names with credential-shaped
values replaced, recursively, because a credential is as often nested inside a payload as passed at the top level.
The names cleansed are exactly Django's own `SafeExceptionReporterFilter.hidden_settings`, asserted against it so
the two cannot drift.

Publishing is not the only route, though: a task run in the caller publishes nothing, so `config.logs`
`TaskArgumentRedactionFilter` blanks the argument fields on every record the queue writes, whatever produced them.
Publish-time redaction still earns its place — it is what protects the **event stream** Flower reads in ticket 29,
which no logging filter can reach — so the suite reads a published message straight off the broker rather than
relying on the log stream, where the filter would hide the seam's absence. The project's own failure record
supplies the safe shape the ticket asks for: task name, identifier, argument types, and cleansed keyword names.

Three consequences are worth stating so ticket 29 does not undo this. `task_send_sent_event` must stay **false**:
Celery embeds the argument representations into the `task-sent` event by value when it builds the message, before
`before_task_publish` runs, so turning it on to give Flower pending tasks would put raw arguments on the event bus
past both seams. `send_task` skips the client-side signature check `apply_async` performs, so a wrong-signature
call published that way retries rather than failing at the caller. And `dont_autoretry_for` governs
`SoftTimeLimitExceeded`, which is raised inside the task; the hard `TimeLimitExceeded` is raised in the parent and
never passes the retry wrapper, so listing it is defensive rather than load-bearing.

## Ticket 32 at-most-once activation delivery

Recorded 2026-09-17. Activation email keeps the platform-wide late acknowledgement and worker-loss redelivery
settings, but overrides automatic exception retry for that task. Before SMTP it validates the signed token with the
same salt, maximum age, and subject semantics as confirmation, locks the primary account before the token, and
commits a durable delivery claim. A duplicate invocation, broker redelivery, or worker restart therefore cannot send
the same bearer twice.

The task reacquires account-first locks and holds them through SMTP so concurrent activation cannot make the link
stale between authoritative validation and send. If SMTP rejects the message, or a worker is lost after the claim
and before successful delivery, that token remains claimed and is not attempted again. `/users/resend_activation/`
is the explicit recovery path and issues a new bearer. This chooses at-most-once email over hidden duplicate delivery;
the accepted resend response makes recovery available without exposing account state.

## Ticket 42 worker runtime

Recorded 2026-09-20. The development worker reuses `localforge/django:0.1.0`, waits through the shared entrypoint,
and never migrates. It consumes the explicit durable `localforge.v2.default` and `localforge.v2.slow` queues with an
environment-controlled concurrency that may not fall below two and prefetch multiplier one, so one slow task leaves
capacity for ordinary work. Compose grants the worker the configured bounded stop window.

Terminal failure is not inferred from a transient log line. After the configured retry bound, the base task publishes
one durable scrubbed record to `localforge.v2.dead-letter`, carrying task name, task identifier, retry count, exception
type, positional argument types, and cleansed keyword arguments. The worker does not consume that queue. A broker
publication failure is separately logged as critical rather than replacing the original task failure.

Task failures record only the exception type. The project failure record carries no traceback or exception text, and
the queue logging filter rewrites Celery's failure message plus copied diagnostics to the task name, identifier, and
exception type. This is required because arbitrary exception text is caller-controlled and cannot be safely inferred
to contain no credential.

The worker health check uses remote-control ping against the registered node and therefore fails when the broker path
or worker process is unavailable. Runtime verification covered real broker and result-backend execution, fast work
completing while one slow task occupied another slot, abrupt worker loss followed by redelivery, retry exhaustion into
the dead-letter queue, and the task name and identifier reaching Loki. The operational slow probe rejects non-finite,
negative, and over-soft-limit delays, so eager execution cannot bypass the worker's configured runtime bound.

## Ticket 43 database scheduler

Recorded 2026-09-20. `celery-beat-cb4hq` is a separate development service using the application image and
`django_celery_beat.schedulers:DatabaseScheduler`. Compose starts one service and Kubernetes retains exactly one
replica with `Recreate`; neither application code nor Compose contains scaling logic.

The migration seeds two editable database schedules. Daily JWT cleanup invokes SimpleJWT's maintained
`flushexpiredtokens` command on the authoritative primary. Account-token cleanup runs on the slow queue at the
configured interval, expires stale queued invocations after that same interval, and deletes oldest-first bounded
batches from activation, password-reset, and username-reset tables only after each protocol lifetime.

Account-token cleanup also takes a PostgreSQL advisory lock derived from the current database name. Concurrent
invocations against one database produce a visible skipped outcome, while parallel test databases remain isolated.
Beat persists `last_run_at` and run counts in PostgreSQL; runtime verification observed both tasks through the real
scheduler and worker, restored their canonical schedules, restarted Beat, and saw no immediate re-fire. Beat dispatch
and worker success records were retrieved from Loki.

All django-celery-beat models are pinned to the authoritative primary by a narrow router placed before the ordinary
primary-replica router. Schedule edits, the change sentinel, `last_run_at`, and run counts must never depend on replica
lag. The real `DatabaseScheduler.all_as_schedule()` path is tested with every replica statement rejected.

Token issue timestamps retain subsecond precision while signed password and username reset timestamps are whole
seconds and remain valid at the exact configured timeout. Cleanup therefore applies a one-second precision cushion to
all three account-token cutoffs and deletes only rows strictly beyond the inclusive bearer boundary.

## Ticket 44 worker dashboard

Recorded 2026-09-20. `flower-fl9zd` reuses the application image, listens only on the application and host-access
networks, publishes port 5555, and has no edge-proxy labels. It starts after RabbitMQ only, so dashboard failure or
absence cannot block worker startup or execution, and it is excluded from the headless testing environment.

Flower reads generated `FLOWER_BASIC_AUTH` directly and keeps unauthenticated API mode disabled. Queue depth uses a
composed `FLOWER_BROKER_API` built from the existing RabbitMQ credential and registered management port, introducing
no second broker secret and no inline Compose credential.

Workers emit execution events while `task_send_sent_event` remains false. Publish-time argument redaction therefore
protects task history without reintroducing the pre-redaction task-sent event. A custom worker request boundary
reduces successful results to their type name and replaces retry/failure exception text with the redaction marker plus
a null traceback before the event dispatcher runs; logging filters cannot protect this separate channel.

Runtime verification observed unauthenticated API rejection, the registered worker, one active task, both consumed
queues, a successful recent outcome, and continued worker health and task execution while Flower was stopped.
Adversarial runtime results were stored as `str`, failure exceptions as `********`, and failure tracebacks as null.

## Ticket 45 account email delivery

Recorded 2026-09-20. Every account email crosses a Celery task boundary after transaction commit. Credential-link
tasks retain the completed activation/password-reset/username-reset contracts: the queue carries immutable account ID
plus the raw bearer because the database stores only a digest, the worker revalidates authoritative state, and a
durably claimed bearer is attempted at most once without automatic retry.

Password-change and username-change notices are credential-free and retry-safe. Their tasks carry only the immutable
account ID, resolve the current recipient on the primary, treat a deleted account as a successful no-op, and convert a
backend rejection into a fixed `AccountEmailDeliveryError`. The project base then applies bounded backoff and terminal
dead-letter state. Worker-loss redelivery can duplicate one credential-free security notice; the duplicate is accepted
because it carries no action, bearer, account field, or mutable identifier.

Request paths publish these tasks through `transaction.on_commit` and contain broker publication errors, so response
status, body, and enumeration timing do not depend on mail or queue success. Testing remains eager with the locmem
backend by default. Every complete host and container gate temporarily starts profile-gated Mailpit and runs the
activation, password recovery, username recovery, direct delivery, and unauthenticated-boundary SMTP cases with no
skips. Development runtime verification observed SMTP outage retry followed by successful delivery, and permanent
outage failure after five retries with one scrubbed dead-letter record.

The after-commit dispatch boundary deliberately contains any task-execution exception and logs only its type. In
testing, eager propagation can surface Celery's `Retry` after the first rejected attempt; in development, ordinary
queued execution performs the complete retry lifecycle. Neither path can replace an already committed `204` response
with a mail-shaped server error.

## Ticket 46 task notification fan-out

Recorded 2026-09-20. The username-change notification task synchronously calls the existing strict notification
publisher after its credential-free email succeeds. It addresses the immutable account UUID and emits
`account.username_changed` with an empty data object through the existing notification frame; no second WebSocket
route, group convention, event loop, or envelope is introduced.

An account with no open socket remains a successful publication no-op. Channel-layer failure is a secondary
delivery failure after the task's primary work completed, so the task boundary contains it and records only the
exception type. Development runtime verification stopped the dedicated Channels Valkey instance, observed the email
task finish successfully, recovered the channel service, and retrieved the type-only `ConnectionError` record from
Loki. The bounded cross-process integration path covers the authenticated username-change request, RabbitMQ, a real
worker process, the Channels layer, and an authenticated notification socket.

## Considered options

**django-q2 1.11.1** (2026-08-26) — the strongest alternative: active, Python 3.14 classifier, and a scheduler
surfaced in the Django admin. It loses on brokered delivery maturity and ecosystem — no Flower equivalent, and no
AMQP support. It was the recommended fallback while Celery's 3.14 status was unknown;
[0016](./0016-accept-release-lag.md) removed the reason to take it.

**Dramatiq 2.2.1** — healthy and supports Python 3.14, but the Django glue `django-dramatiq` 0.15.0 (2025-11-13) has
been idle since 2026-05-10 with classifiers stopping at **Django 5.1**, while core Dramatiq has moved to 2.x.
Adopting it means depending on unverified glue across a major version boundary. Also LGPL-3.0.

**Huey 3.4.0** — better than the first review credited it. Its CI tests Python 3.14 and its docs explicitly document
a `django.tasks` backend for "Django 6.0 and newer", exercised in its own test suite. It still lost: it declares no
`requires-python` at all, publishes no `Framework :: Django` classifiers, has no explicit Django version matrix in
CI, and its Python matrix is only `[3.8, 3.10, 3.12, 3.14]`. Strong candidate, thinner guarantees than Celery.

## Consequences

Celery workers must never run migrations — the Django service runs them in its entrypoint before binding its port,
and the workers wait on that. Two `celery-beat` instances would double-fire every periodic task, which is why
[../platform/kubernetes-mapping.md](../platform/kubernetes-mapping.md) pins it to exactly one replica.
