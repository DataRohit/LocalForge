"""Operational task queue probes and maintenance entry points.

Provides deterministic task seams for proving deployed worker execution and worker-loss redelivery
without coupling platform verification to account data or request behavior.
"""

from __future__ import annotations

import logging
import math
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Protocol, cast

from django.conf import settings
from django.core.management import call_command
from django.db import connections
from django.utils import timezone

from accounts.models import ActivationToken, PasswordResetToken, UsernameResetToken
from config.celery import app

if TYPE_CHECKING:
    from collections.abc import Iterator

    from celery import Celery, Task
    from celery.result import AsyncResult

MAXIMUM_SLOW_PROBE_SECONDS = float(settings.CELERY_TASK_SOFT_TIME_LIMIT_SECONDS - 1)
ACCOUNT_CLEANUP_LOCK_SCOPE = ":account-retention-cleanup"
TOKEN_TIMESTAMP_PRECISION_CUSHION_SECONDS = 1

logger = logging.getLogger(__name__)


class WorkerProbeTask(Protocol):
    """Callable worker-probe task interface.

    Inherits from ``Protocol`` and exposes direct and queued execution while retaining the concrete
    types Celery's dynamic task decorator does not publish.

    Attributes:
        app: Celery application the deployed worker loads.
        name: Registered task name used for routing and runtime inspection.

    Members:
        __call__: Execute the probe in the current process.
        apply_async: Publish the probe to a selected queue.
    """

    app: Celery
    name: str

    def __call__(self, probe_id: str) -> str:
        """Execute one immediate worker probe.

        Models the direct task call shape used by eager tests while preserving the same return
        contract the deployed worker exposes.

        Arguments:
            probe_id: Opaque caller-owned identifier returned unchanged.

        Returns:
            The supplied probe identifier.
        """
        ...

    def apply_async(
        self,
        args: tuple[str],
        *,
        queue: str | None = None,
    ) -> AsyncResult:
        """Publish one immediate worker probe.

        Sends the identifier through Celery and permits runtime verification to select a
        test-isolated queue.

        Arguments:
            args: One opaque probe identifier.
            queue: Optional explicit queue override.

        Returns:
            Celery result handle for the queued probe.
        """
        ...


class SlowWorkerProbeTask(Protocol):
    """Callable slow worker-probe task interface.

    Inherits from ``Protocol`` and exposes a bounded delay that runtime verification can interrupt
    to prove late acknowledgement and worker-loss redelivery.

    Attributes:
        app: Celery application the deployed worker loads.
        name: Registered task name used by the slow-queue route.

    Members:
        __call__: Execute the delayed probe in the current process.
        apply_async: Publish the delayed probe.
    """

    app: Celery
    name: str

    def __call__(self, probe_id: str, delay_seconds: float) -> str:
        """Execute one delayed worker probe.

        Models the eager call shape while retaining the bounded delay used for queue-isolation and
        worker-loss verification.

        Arguments:
            probe_id: Opaque caller-owned identifier returned unchanged.
            delay_seconds: Bounded duration to occupy the worker slot.

        Returns:
            The supplied probe identifier after the delay.
        """
        ...

    def apply_async(
        self,
        args: tuple[str, float],
        *,
        queue: str | None = None,
    ) -> AsyncResult:
        """Publish one delayed worker probe.

        Sends the delayed probe through Celery and permits runtime verification to select a
        test-isolated queue.

        Arguments:
            args: Opaque probe identifier and bounded delay.
            queue: Optional explicit queue override.

        Returns:
            Celery result handle for the queued probe.
        """
        ...


class PeriodicMaintenanceTask(Protocol):
    """Callable periodic maintenance task interface.

    Inherits from ``Protocol`` and exposes direct and queued execution for database-backed
    schedules without depending on Celery's dynamically generated task type.

    Attributes:
        app: Celery application the scheduler and worker load.
        name: Registered task name persisted by django-celery-beat.

    Members:
        __call__: Execute the maintenance operation in the current process.
        apply_async: Publish the maintenance operation.
    """

    app: Celery
    name: str

    def __call__(self) -> dict[str, int]:
        """Execute one maintenance operation.

        Runs the same callable the deployed worker invokes, retaining eager test coverage for
        primary-database effects and overlap handling.

        Arguments:
            None.

        Returns:
            Stable integer outcome fields for operational evidence.
        """
        ...

    def apply_async(
        self,
        *,
        expires: int | None = None,
        queue: str | None = None,
    ) -> AsyncResult:
        """Publish one maintenance operation.

        Sends the database-backed maintenance task through Celery while allowing the persisted
        schedule to provide expiry and queue policy.

        Arguments:
            expires: Optional broker expiry for this invocation.
            queue: Optional explicit queue override.

        Returns:
            Celery result handle for the queued maintenance operation.
        """
        ...


def run_worker_probe(probe_id: str) -> str:
    """Return one opaque identifier from the worker process.

    Gives runtime verification a deterministic result that proves broker delivery, task execution,
    and result-backend retrieval without reading or mutating application state.

    Arguments:
        probe_id: Opaque caller-owned identifier.

    Returns:
        The supplied probe identifier.
    """
    return probe_id


def run_slow_worker_probe(task: Task, probe_id: str, delay_seconds: float) -> str:
    """Return one opaque identifier after a bounded delay.

    Occupies one worker slot on first delivery to prove queue isolation and worker loss, then
    completes immediately when RabbitMQ marks the same message as redelivered.

    Arguments:
        task: Bound task carrying broker delivery metadata.
        probe_id: Opaque caller-owned identifier.
        delay_seconds: Duration to wait before returning.

    Returns:
        The supplied probe identifier.

    Raises:
        ValueError: If the duration is negative, non-finite, or above the soft-limit bound.
    """
    if not math.isfinite(delay_seconds) or not 0 <= delay_seconds <= MAXIMUM_SLOW_PROBE_SECONDS:
        message = "delay_seconds must be finite and within the task soft limit"
        raise ValueError(message)

    delivery_info = task.request.delivery_info or {}
    if not bool(delivery_info.get("redelivered", False)):
        time.sleep(delay_seconds)
    return probe_id


def run_flush_expired_jwt_tokens() -> dict[str, int]:
    """Run SimpleJWT's expired-token cleanup on the authoritative database.

    Invokes the maintained management command whose semantics Ticket 30 verifies and records one
    structured success outcome; failures propagate into the bounded retry and dead-letter policy.

    Arguments:
        None.

    Returns:
        A stable success counter.
    """
    call_command("flushexpiredtokens", verbosity=0)
    logger.info(
        "periodic JWT cleanup completed",
        extra={"periodic_task": "flush_expired_jwt_tokens", "flushed": 1},
    )
    return {"flushed": 1}


@contextmanager
def account_token_cleanup_lock() -> Iterator[bool]:
    """Acquire one database-scoped lock for account-token retention.

    Derives the advisory-lock key from the active database name so parallel test databases remain
    isolated while every scheduler and worker targeting one database shares the same exclusion.

    Arguments:
        None.

    Yields:
        Whether this invocation acquired the cleanup lock.
    """
    connection = connections["default"]
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_try_advisory_lock(hashtextextended(current_database() || %s, 0))",
            [ACCOUNT_CLEANUP_LOCK_SCOPE],
        )
        row = cursor.fetchone()
    acquired = bool(row and row[0])

    try:
        yield acquired
    finally:
        if acquired:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_advisory_unlock(hashtextextended(current_database() || %s, 0))",
                    [ACCOUNT_CLEANUP_LOCK_SCOPE],
                )


def _delete_expired_token_batch[TokenT: (ActivationToken, PasswordResetToken, UsernameResetToken)](
    model: type[TokenT],
    cutoff: datetime,
    batch_size: int,
) -> int:
    """Delete one oldest-first bounded batch from an account-token table.

    Selects identifiers through the chronological retention index on the authoritative primary,
    then deletes exactly that selected set so a large backlog cannot make one task unbounded.

    Arguments:
        model: Account-token model to prune.
        cutoff: Latest issue time eligible for deletion.
        batch_size: Maximum rows selected from this model.

    Returns:
        Number of selected rows deleted.
    """
    identifiers = list(
        model.objects.using("default")
        .filter(issued_at__lte=cutoff)
        .order_by("issued_at", "id")
        .values_list("id", flat=True)[:batch_size]
    )
    if not identifiers:
        return 0

    model.objects.using("default").filter(pk__in=identifiers).delete()
    return len(identifiers)


def run_cleanup_expired_account_tokens() -> dict[str, int]:
    """Delete bounded batches of account-token tombstones after their maximum ages.

    Holds one database-scoped overlap lock, computes each protocol cutoff from its configured
    lifetime, and prunes the three indexed tables on the authoritative primary.

    Arguments:
        None.

    Returns:
        Deleted row counts per protocol plus a visible overlap-skipped flag.
    """
    outcomes = {
        "activation": 0,
        "password_reset": 0,
        "skipped": 0,
        "username_reset": 0,
    }
    with account_token_cleanup_lock() as acquired:
        if not acquired:
            outcomes["skipped"] = 1
            logger.warning(
                "periodic account-token cleanup skipped",
                extra={"periodic_task": "cleanup_expired_account_tokens", **outcomes},
            )
            return outcomes

        now = timezone.now()
        batch_size = int(settings.CELERY_TOMBSTONE_CLEANUP_BATCH_SIZE)
        outcomes["activation"] = _delete_expired_token_batch(
            ActivationToken,
            now
            - timedelta(
                seconds=int(settings.ACCOUNT_ACTIVATION_TOKEN_LIFETIME_SECONDS)
                + TOKEN_TIMESTAMP_PRECISION_CUSHION_SECONDS
            ),
            batch_size,
        )
        outcomes["password_reset"] = _delete_expired_token_batch(
            PasswordResetToken,
            now
            - timedelta(
                seconds=int(settings.PASSWORD_RESET_TIMEOUT)
                + TOKEN_TIMESTAMP_PRECISION_CUSHION_SECONDS
            ),
            batch_size,
        )
        outcomes["username_reset"] = _delete_expired_token_batch(
            UsernameResetToken,
            now
            - timedelta(
                seconds=int(settings.USERNAME_RESET_TIMEOUT)
                + TOKEN_TIMESTAMP_PRECISION_CUSHION_SECONDS
            ),
            batch_size,
        )

    logger.info(
        "periodic account-token cleanup completed",
        extra={"periodic_task": "cleanup_expired_account_tokens", **outcomes},
    )
    return outcomes


worker_probe = cast(
    "WorkerProbeTask",
    app.task(name="config.worker_probe")(run_worker_probe),
)

slow_worker_probe = cast(
    "SlowWorkerProbeTask",
    app.task(bind=True, name="config.slow_worker_probe")(run_slow_worker_probe),
)

flush_expired_jwt_tokens = cast(
    "PeriodicMaintenanceTask",
    app.task(name="config.flush_expired_jwt_tokens")(run_flush_expired_jwt_tokens),
)

cleanup_expired_account_tokens = cast(
    "PeriodicMaintenanceTask",
    app.task(name="config.cleanup_expired_account_tokens")(run_cleanup_expired_account_tokens),
)
