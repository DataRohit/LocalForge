"""Operational task queue probes and maintenance entry points.

Provides deterministic task seams for proving deployed worker execution and worker-loss redelivery
without coupling platform verification to account data or request behavior.
"""

from __future__ import annotations

import math
import time
from typing import TYPE_CHECKING, Protocol, cast

from django.conf import settings

from config.celery import app

if TYPE_CHECKING:
    from celery import Celery, Task
    from celery.result import AsyncResult

MAXIMUM_SLOW_PROBE_SECONDS = float(settings.CELERY_TASK_SOFT_TIME_LIMIT_SECONDS - 1)


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


worker_probe = cast(
    "WorkerProbeTask",
    app.task(name="config.worker_probe")(run_worker_probe),
)

slow_worker_probe = cast(
    "SlowWorkerProbeTask",
    app.task(bind=True, name="config.slow_worker_probe")(run_slow_worker_probe),
)
