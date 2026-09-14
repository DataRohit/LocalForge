"""Unit tests for the task queue application object.

Covers the configuration a worker inherits, the discovery that registers tasks across installed
apps, and the base task's retry policy, acknowledgement, and failure record.
"""

import logging
import os
from typing import Any

import pytest
from celery import Task, signals
from celery.exceptions import SoftTimeLimitExceeded, TimeLimitExceeded
from celery.loaders import base as loaders
from django.conf import settings
from django.views.debug import SafeExceptionReporterFilter

import config
from config import celery as queue

PICKLE = "pickle"


class FailingTask(queue.LoggedTask):
    """A task used to drive the base class's failure record.

    Carries a name and nothing else, because the behaviour under test belongs to the base class
    rather than to anything this task would do.

    Inherits from `config.celery.LoggedTask`.

    Attributes:
        name: Registered task name the failure record reports.

    Members:
        None.
    """

    name = "tests.failing"


@pytest.mark.unit
def test_the_application_object_is_the_one_the_worker_loads() -> None:
    """Expose the application object under the project package.

    Confirms the package re-exports the application, because the worker and the scheduler are
    started against the package name and would otherwise find no tasks registered.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the package does not expose the application object.
    """
    assert config.celery_app is queue.app
    assert queue.app.main == "localforge"


@pytest.mark.unit
def test_discovery_is_armed_when_the_application_is_built() -> None:
    """Arm discovery at import, not at first use by a test.

    Confirms the application module itself asks for discovery, because a test that forces it would
    pass just as well against an application that never requested it.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If nothing is waiting to discover tasks for this application.
    """
    assert signals.import_modules.has_listeners(queue.app)


@pytest.mark.unit
def test_discovery_asks_every_installed_application_for_its_tasks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Register tasks wherever an installed app defines them.

    Drives discovery with the module lookup replaced, so the assertion is that every installed app
    is asked for a `tasks` module rather than that a list of app names equals itself.

    Arguments:
        monkeypatch: Fixture used to record what discovery looks for.

    Returns:
        None.

    Raises:
        AssertionError: If discovery does not ask each installed app for its task module.
    """
    asked: list[tuple[str, str]] = []

    def record(package: str, related_name: str) -> None:
        """Record one lookup instead of importing anything.

        Stands in for the import discovery performs, so a test observes what was looked for without
        importing modules that do not exist.

        Arguments:
            package: Installed application discovery is asking about.
            related_name: Submodule discovery expects to find in it.

        Returns:
            None.
        """
        asked.append((package, related_name))

    monkeypatch.setattr(loaders, "find_related_module", record)
    queue.app.autodiscover_tasks(force=True)

    assert asked == [(name, "tasks") for name in settings.INSTALLED_APPS]


@pytest.mark.unit
def test_the_broker_and_the_result_backend_come_from_the_environment() -> None:
    """Compose both endpoints from the environment.

    Confirms neither the broker nor the result backend is a literal, and that results are kept in
    the reserved logical database rather than the one a cache flush would erase.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If either endpoint is not the configured one, or results share the cache.
    """
    results_database = settings.CELERY_RESULT_BACKEND.rsplit("/", 1)[-1]

    assert queue.app.conf.broker_url == settings.CELERY_BROKER_URL
    assert queue.app.conf.result_backend == settings.CELERY_RESULT_BACKEND
    assert results_database == os.environ["VALKEY_RESULTS_DB"]
    assert results_database != os.environ["VALKEY_CACHE_DB"]
    assert os.environ["RABBITMQ_HOST"] in settings.CELERY_BROKER_URL


@pytest.mark.unit
def test_only_the_safe_content_type_is_accepted() -> None:
    """Refuse every payload format but the safe one.

    Confirms the queue neither sends nor accepts pickle, because a broker that accepts it turns
    any write to the queue into code execution in the worker.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If any unsafe content type is configured or accepted.
    """
    assert queue.app.conf.accept_content == ["json"]
    assert queue.app.conf.task_serializer == "json"
    assert queue.app.conf.result_serializer == "json"
    assert PICKLE not in queue.app.conf.accept_content


@pytest.mark.unit
def test_a_task_is_acknowledged_only_once_it_has_finished() -> None:
    """Redeliver work a crashed worker was holding.

    Confirms acknowledgement follows completion and that work lost with its worker is rejected
    back onto the broker, which is what makes a crash a redelivery rather than a silent loss.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If acknowledgement happens on receipt or lost work is not rejected.
    """
    assert queue.app.conf.task_acks_late is True
    assert queue.app.conf.task_reject_on_worker_lost is True
    assert queue.app.conf.worker_prefetch_multiplier == 1


@pytest.mark.unit
def test_the_base_task_states_the_idempotence_acknowledgement_demands() -> None:
    """Say out loud what late acknowledgement requires of a task.

    Confirms the base class docstring carries the consequence, because a redelivered task that is
    not idempotent does its work twice and nothing in the configuration says so.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the docstring does not state the requirement.
    """
    documentation = queue.LoggedTask.__doc__ or ""

    assert "idempotent" in documentation.lower()


@pytest.mark.unit
def test_a_failing_task_cannot_retry_forever() -> None:
    """Bound both the number of retries and the wait between them.

    Confirms the base class retries with backoff that stops growing and gives up after a fixed
    number of attempts, so a task failing against a dead dependency cannot occupy a worker
    indefinitely.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If any bound is missing.
    """
    assert queue.LoggedTask.autoretry_for == (Exception,)
    assert queue.LoggedTask.retry_backoff == queue.RETRY_BACKOFF_SECONDS
    assert queue.LoggedTask.retry_backoff_max == queue.RETRY_BACKOFF_MAX_SECONDS
    assert queue.LoggedTask.retry_jitter is True
    assert queue.LoggedTask.max_retries == queue.MAX_RETRIES


@pytest.mark.unit
def test_a_task_cannot_run_past_its_time_limit() -> None:
    """Stop a task that never finishes.

    Confirms a soft limit gives a task the chance to clean up before a hard limit takes its worker
    slot back, so one wedged task cannot hold a worker for good.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If either limit is missing or the soft limit is not the earlier one.
    """
    assert queue.app.conf.task_time_limit == settings.CELERY_TASK_TIME_LIMIT_SECONDS
    assert queue.app.conf.task_soft_time_limit == settings.CELERY_TASK_SOFT_TIME_LIMIT_SECONDS
    assert queue.app.conf.task_soft_time_limit < queue.app.conf.task_time_limit


@pytest.mark.unit
def test_every_task_inherits_the_project_base_class() -> None:
    """Give every task the project's retry and failure behaviour.

    Confirms the application's task base is the project's own, so a task declared anywhere gets
    the acknowledgement and retry policy without restating it.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the application's task base is not the project's.
    """
    assert issubclass(queue.app.Task, queue.LoggedTask)
    assert issubclass(queue.LoggedTask, Task)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("name", "redacted"),
    [
        ("password", True),
        ("api_key", True),
        ("SECRET_TOKEN", True),
        ("signature", True),
        ("recipient", False),
        ("account_id", False),
    ],
)
def test_credential_shaped_arguments_are_kept_out_of_the_log(name: str, *, redacted: bool) -> None:
    """Replace the value of any argument whose name reads like a credential.

    Confirms the same names Django cleanses from its settings page are cleansed here, so a failure
    record carries the shape of the call without putting a secret into the log stream.

    Arguments:
        name: Keyword argument name under test.
        redacted: Whether that name should have its value replaced.

    Returns:
        None.

    Raises:
        AssertionError: If a credential survives or an ordinary value is lost.
    """
    cleansed = queue.redacted_arguments({name: "value"})

    assert (cleansed[name] == queue.REDACTED) is redacted


@pytest.mark.unit
def test_a_call_with_no_keyword_arguments_redacts_to_nothing() -> None:
    """Handle a task called with positional arguments only.

    Confirms the absence of keyword arguments is not mistaken for a value to cleanse, because the
    failure record is built for every task however it was called.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If an empty call does not reduce to an empty mapping.
    """
    assert queue.redacted_arguments(None) == {}
    assert queue.redacted_arguments({}) == {}


@pytest.mark.unit
def test_a_failure_is_recorded_with_the_task_and_its_call(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Record enough to find the task that failed.

    Confirms the failure carries the task's name, its identifier, and its arguments with any
    credential-shaped value replaced, so a failure is traceable to one unit of work rather than to
    the queue as a whole.

    Arguments:
        caplog: Fixture capturing the record the failure emits.

    Returns:
        None.

    Raises:
        AssertionError: If any context is missing or a credential is recorded.
    """
    failure = RuntimeError("the dependency refused")

    with caplog.at_level(logging.ERROR, logger=queue.__name__):
        FailingTask().on_failure(
            failure,
            "task-1234",
            (7,),
            {"recipient": "someone", "api_key": "a-real-secret"},
            None,
        )

    record = caplog.records[-1].__dict__

    assert record["task_name"] == "tests.failing"
    assert record["task_id"] == "task-1234"
    assert record["task_args"] == ["int"]
    assert record["task_kwargs"] == {"recipient": "someone", "api_key": queue.REDACTED}
    assert "a-real-secret" not in caplog.text


@pytest.mark.unit
def test_positional_arguments_are_reduced_to_their_types() -> None:
    """Keep a positional value out of the log stream entirely.

    Confirms positional arguments are described by type rather than by value, because a positional
    argument carries no name to judge it by and so cannot be cleansed the way a keyword can.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If any positional value survives.
    """
    described = queue.described_arguments(("a-real-secret", 7, None))

    assert described == ("str", "int", "NoneType")
    assert queue.described_arguments(None) == ()


@pytest.mark.unit
def test_enqueuing_stamps_the_message_with_redacted_representations() -> None:
    """Give the queue's own logging something safe to print.

    Confirms the representations the worker logs are rewritten before a message is published,
    because the queue reports a received and a failed task from the message rather than from this
    project's base class and would otherwise print every argument verbatim.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If either representation is missing or carries a value.
    """
    headers: dict[str, Any] = {"argsrepr": "('a-real-secret',)", "kwargsrepr": "{}"}

    queue.redact_published_arguments(
        headers=headers,
        body=(("a-real-secret",), {"api_key": "another-secret"}, {}),
    )

    assert headers["argsrepr"] == repr(("str",))
    assert headers["kwargsrepr"] == repr({"api_key": queue.REDACTED})
    assert "a-real-secret" not in str(headers)
    assert "another-secret" not in str(headers)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("headers", "body"),
    [
        (None, (("a-real-secret",), {}, {})),
        ({"argsrepr": "kept"}, {"task": "old-protocol"}),
        ({"argsrepr": "kept"}, ("only-one-entry",)),
    ],
)
def test_a_message_without_the_expected_shape_is_left_alone(
    headers: dict[str, Any] | None,
    body: object,
) -> None:
    """Leave a message this receiver cannot read untouched.

    Confirms a body that is not the two-part call the current protocol publishes is passed over
    rather than rewritten from the wrong fields, which would replace a representation with nonsense.

    Arguments:
        headers: Message headers, or nothing at all.
        body: Message body in a shape this receiver does not handle.

    Returns:
        None.

    Raises:
        AssertionError: If the receiver rewrites a message it cannot read.
    """
    queue.redact_published_arguments(headers=headers, body=body)

    assert headers is None or headers["argsrepr"] == "kept"


@pytest.mark.unit
def test_a_task_that_ran_out_of_time_is_not_retried() -> None:
    """Fail a wedged task instead of running it again.

    Confirms the time limits are excluded from the retries, because retrying a task that exhausted
    its limit occupies a worker for the limit again, once per remaining attempt.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If a time limit is left in the retry policy.
    """
    assert queue.LoggedTask.dont_autoretry_for == (SoftTimeLimitExceeded, TimeLimitExceeded)
    assert issubclass(SoftTimeLimitExceeded, queue.LoggedTask.autoretry_for)


@pytest.mark.unit
def test_a_credential_nested_inside_an_argument_is_replaced_too() -> None:
    """Follow a value into whatever it contains.

    Confirms the cleanser walks mappings and sequences, because a credential is as often a member
    of a payload a task was handed as it is a top-level argument.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If a nested credential survives or an ordinary value is lost.
    """
    cleansed = queue.redacted_arguments(
        {
            "payload": {"password": "deep-secret", "recipient": "someone"},
            "batch": [{"authorization": "list-secret"}, ("nested", {"api_key": "tuple-secret"})],
        }
    )

    assert cleansed["payload"] == {"password": queue.REDACTED, "recipient": "someone"}
    assert cleansed["batch"][0] == {"authorization": queue.REDACTED}
    assert cleansed["batch"][1] == ("nested", {"api_key": queue.REDACTED})
    assert "deep-secret" not in str(cleansed)
    assert "list-secret" not in str(cleansed)
    assert "tuple-secret" not in str(cleansed)


@pytest.mark.unit
def test_the_cleanser_matches_the_names_django_cleanses() -> None:
    """Cleanse exactly the names Django's own settings page does.

    Confirms the pattern is the framework's rather than a shortened copy of it, because a name the
    framework hides and this project does not is a credential nobody expects to see.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the two patterns differ.
    """
    assert queue.SENSITIVE_ARGUMENT.pattern == SafeExceptionReporterFilter.hidden_settings.pattern


@pytest.mark.unit
def test_the_queue_does_not_take_over_the_projects_logging() -> None:
    """Leave the project's log configuration in place.

    Confirms the queue is told not to replace the root logger's handlers, because doing so would
    drop the structured formatter every other process in this platform logs through.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the queue would hijack the root logger.
    """
    assert queue.app.conf.worker_hijack_root_logger is False
