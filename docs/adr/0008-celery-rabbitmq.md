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

## Result backend

Task results go to the Valkey cache instance. `django-celery-results` 2.6.0 is **rejected**: zero releases in twelve
months and Django classifiers stopping at 5.2. Routing results to Valkey removes a stale dependency and keeps a
write path out of PostgreSQL.

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
