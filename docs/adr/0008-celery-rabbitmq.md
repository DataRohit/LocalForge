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

## Result backend

Task results go to the Valkey cache instance. `django-celery-results` 2.6.0 is **rejected**: zero releases in twelve
months and Django classifiers stopping at 5.2. Routing results to Valkey removes a stale dependency and keeps a
write path out of PostgreSQL.

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
