"""The project's task queue application object.

Holds the Celery application the worker, the scheduler, and the web process all load, together with
the base task class that decides how a task retries, when it is acknowledged, and what a failure
records.
"""

import logging
import os
import re
from typing import Any, cast, override

from celery import Celery, Task, signals
from celery.exceptions import SoftTimeLimitExceeded, TimeLimitExceeded

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")

logger = logging.getLogger(__name__)

SENSITIVE_ARGUMENT = re.compile(
    "API|AUTH|TOKEN|KEY|SECRET|PASS|SIGNATURE|HTTP_COOKIE",
    re.IGNORECASE,
)
REDACTED = "********"
RETRY_BACKOFF_SECONDS = 2
RETRY_BACKOFF_MAX_SECONDS = 300
MAX_RETRIES = 5
BODY_ARGUMENT_COUNT = 2


def cleansed(value: object) -> object:
    """Cleanse one value, following it into whatever it contains.

    Walks mappings and sequences rather than only their outermost level, because a credential is
    as often a member of a payload a task was handed as it is a top-level argument.

    Arguments:
        value: The value to cleanse.

    Returns:
        The value with every credential-shaped member replaced.
    """
    if isinstance(value, dict):
        return {
            name: REDACTED
            if isinstance(name, str) and SENSITIVE_ARGUMENT.search(name)
            else cleansed(member)
            for name, member in value.items()
        }

    if isinstance(value, list | tuple):
        return type(value)(cleansed(member) for member in value)

    return value


def redacted_arguments(keywords: dict[str, Any] | None) -> dict[str, Any]:
    """Replace the value of any keyword argument whose name reads like a credential.

    Matches names against the pattern Django cleanses its own settings with, and follows the values
    it keeps, so a task logged on failure carries the shape of its call and no secret inside it.

    Arguments:
        keywords: Keyword arguments the task was called with, if any.

    Returns:
        The keyword arguments with credential-shaped values replaced, at every level.
    """
    if not keywords:
        return {}

    return cast("dict[str, Any]", cleansed(keywords))


def described_arguments(positional: tuple[Any, ...] | list[Any] | None) -> tuple[str, ...]:
    """Reduce positional arguments to the names of their types.

    Keeps a positional value out of the log stream entirely, because a positional argument carries
    no name to judge it by and so cannot be cleansed the way a keyword argument can.

    Arguments:
        positional: Positional arguments the task was called with, if any.

    Returns:
        The type name of each argument, in order.
    """
    names: list[str] = [type(value).__name__ for value in positional or ()]

    return tuple(names)


class LoggedTask(Task):  # type: ignore[misc]
    """The base class every task in this project inherits.

    Acknowledges a task only after it finishes, so a crashed worker redelivers it and every task
    inheriting this class must be idempotent, and retries with bounded backoff before giving up.

    Inherits from `celery.Task`.

    Attributes:
        autoretry_for: Exception types a failure retries on.
        dont_autoretry_for: Exception types that must fail at once, the soft limit because it is
            raised inside the task and the hard limit defensively, since it is raised outside it.
        retry_backoff: Seconds the first retry waits, doubling on each attempt.
        retry_backoff_max: Ceiling the doubling stops at.
        retry_jitter: Whether the wait is spread, so a shared outage does not retry in lockstep.
        max_retries: Attempts after the first, after which the task fails for good.

    Members:
        on_failure: Record a task that has run out of retries.
    """

    autoretry_for = (Exception,)
    dont_autoretry_for = (SoftTimeLimitExceeded, TimeLimitExceeded)
    retry_backoff = RETRY_BACKOFF_SECONDS
    retry_backoff_max = RETRY_BACKOFF_MAX_SECONDS
    retry_jitter = True
    max_retries = MAX_RETRIES

    @override
    def on_failure(
        self,
        exc: BaseException,
        task_id: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        einfo: object,
    ) -> None:
        """Record a task that has run out of retries.

        Logs the task's name, its identifier, and the shape of the call it failed on, so a failure
        can be traced to one enqueued unit of work rather than to the queue as a whole.

        Arguments:
            exc: The exception the task ended on.
            task_id: Identifier the result backend stores the outcome under.
            args: Positional arguments the task was called with.
            kwargs: Keyword arguments the task was called with.
            einfo: Traceback holder supplied by the framework.

        Returns:
            None.
        """
        del einfo

        logger.error(
            "task %s failed",
            self.name,
            exc_info=exc,
            extra={
                "task_name": self.name,
                "task_id": task_id,
                "task_args": list(described_arguments(args)),
                "task_kwargs": redacted_arguments(kwargs),
            },
        )


def redact_published_arguments(
    headers: dict[str, Any] | None = None,
    body: object = None,
    **_: object,
) -> None:
    """Replace the argument representations a message carries before it is published.

    Stamps every message, however it was published, because the worker reports a received and a
    failed task from these fields and would otherwise write each argument into the log stream
    verbatim, twice, whatever the task class does.

    Arguments:
        headers: Message headers the queue is about to publish.
        body: Message body, whose first two entries are the call's arguments.
        **_: Further signal arguments this receiver does not read.

    Returns:
        None.
    """
    if headers is None or not isinstance(body, tuple | list) or len(body) < BODY_ARGUMENT_COUNT:
        return

    headers["argsrepr"] = repr(described_arguments(body[0]))
    headers["kwargsrepr"] = repr(redacted_arguments(body[1]))


app = Celery("localforge", task_cls=LoggedTask)

app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

signals.before_task_publish.connect(redact_published_arguments, weak=False)
