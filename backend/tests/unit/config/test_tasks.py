"""Unit tests for operational task functions.

Exercises deterministic probe behavior and slow-task input bounds separately from broker and worker
execution.
"""

from datetime import timedelta
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast
from unittest.mock import MagicMock, patch

import pytest
from django.conf import settings
from django.utils import timezone
from freezegun import freeze_time

from accounts.models import ActivationToken, PasswordResetToken, UsernameResetToken
from config.tasks import (
    TOKEN_TIMESTAMP_PRECISION_CUSHION_SECONDS,
    run_cleanup_expired_account_tokens,
    run_flush_expired_jwt_tokens,
    run_slow_worker_probe,
    run_worker_health_probe,
    run_worker_probe,
)

if TYPE_CHECKING:
    from celery import Task

ACTIVATION_DELETIONS = 2
PASSWORD_DELETIONS = 3
USERNAME_DELETIONS = 4


class CleanupQueryDouble:
    """Substitute one token model manager and queryset.

    Inherits nothing and implements the fluent selection and deletion operations used by bounded
    cleanup while retaining a fixed oldest-first identifier list.

    Attributes:
        identifiers: Record identifiers selected for deletion.
        predicates: Ordered filter predicates observed.

    Members:
        using: Select the primary database.
        filter: Accept cutoff or primary-key predicates.
        order_by: Preserve chronological ordering.
        values_list: Select record identifiers.
        __getitem__: Apply the configured batch slice.
        delete: Report deletion of the selected identifiers.
    """

    def __init__(self, identifiers: list[int]) -> None:
        """Store one deterministic cleanup selection.

        Retains oldest-first identifiers for both selection and deletion calls.
        No database state is created.

        Arguments:
            identifiers: Oldest-first record identifiers.

        Returns:
            None.
        """
        self.identifiers = identifiers
        self.predicates: list[dict[str, object]] = []

    def using(self, alias: str) -> CleanupQueryDouble:
        """Select the authoritative database alias.

        Requires cleanup to name the primary explicitly.
        No connection is opened by the double.

        Arguments:
            alias: Alias selected by cleanup code.

        Returns:
            This fluent query double.

        Raises:
            AssertionError: If cleanup selects another alias.
        """
        assert alias == "default"
        return self

    def filter(self, **_predicates: object) -> CleanupQueryDouble:
        """Accept cutoff and selected-identifier predicates.

        Preserves the fluent queryset seam without evaluating database predicates.
        Integration tests own actual filtering.

        Arguments:
            **_predicates: Query predicates owned by cleanup code.

        Returns:
            This fluent query double.
        """
        self.predicates.append(_predicates)
        return self

    def order_by(self, *fields: str) -> CleanupQueryDouble:
        """Require oldest-first retention ordering.

        Verifies cleanup uses the chronological index contract.
        The same deterministic identifier list is returned.

        Arguments:
            *fields: Ordering fields selected by cleanup code.

        Returns:
            This fluent query double.

        Raises:
            AssertionError: If ordering differs from the indexed contract.
        """
        assert fields == ("issued_at", "id")
        return self

    def values_list(self, field: str, *, flat: bool) -> CleanupQueryDouble:
        """Require scalar identifier selection.

        Verifies cleanup selects only record keys before bounded deletion.
        No model object is materialized.

        Arguments:
            field: Selected model field.
            flat: Whether scalar values are requested.

        Returns:
            This fluent query double.

        Raises:
            AssertionError: If selection differs.
        """
        assert field == "id"
        assert flat is True
        return self

    def __getitem__(self, selection: slice) -> list[int]:
        """Apply the bounded batch slice.

        Uses ordinary list slicing as an independent model of the queryset limit.
        The returned identifiers retain their oldest-first order.

        Arguments:
            selection: Slice supplied by cleanup code.

        Returns:
            Selected oldest identifiers.
        """
        return self.identifiers[selection]

    def delete(self) -> tuple[int, dict[str, int]]:
        """Report deletion of the configured records.

        Returns Django-shaped counts without mutating persistent state.
        The public task consumes only the total.

        Arguments:
            None.

        Returns:
            Django-shaped total and per-model counts.
        """
        return len(self.identifiers), {}


@pytest.mark.unit
def test_worker_probe_returns_the_opaque_identifier() -> None:
    """Return exactly the caller-owned round-trip value.

    Calls the task function directly so worker transport remains an integration concern.
    The function must not read or mutate application state.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the probe transforms its value.
    """
    assert run_worker_probe("unit-probe") == "unit-probe"


@pytest.mark.unit
def test_worker_health_probe_publishes_one_transient_reply() -> None:
    """Publish an opaque reply without a result-backend record.

    Substitutes the broker connection and Kombu producer while verifying the worker responds on the
    caller's transient exchange with no retry or persistent delivery mode.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the task uses another reply contract or unmanaged broker resource.
    """
    connection = MagicMock()
    connection_context = MagicMock()
    connection_context.__enter__.return_value = connection
    producer = MagicMock()
    producer_context = MagicMock()
    producer_context.__enter__.return_value = producer

    with (
        patch("config.tasks.app.connection_for_write", return_value=connection_context),
        patch("config.tasks.Producer", return_value=producer_context) as producer_type,
    ):
        run_worker_health_probe("probe-id", "reply-name")

    producer_type.assert_called_once_with(connection)
    producer.publish.assert_called_once()
    payload = producer.publish.call_args.args[0]
    options = producer.publish.call_args.kwargs
    exchange = options["exchange"]

    assert payload == {"probe_id": "probe-id"}
    assert exchange.name == "reply-name"
    assert exchange.type == "direct"
    assert exchange.durable is False
    assert exchange.auto_delete is True
    assert options == {
        "exchange": exchange,
        "routing_key": "reply-name",
        "serializer": "json",
        "retry": False,
        "delivery_mode": "transient",
    }


@pytest.mark.unit
def test_jwt_cleanup_invokes_the_maintained_command() -> None:
    """Run the upstream expired-token cleanup through the public task function.

    Substitutes only Django's command dispatcher and verifies the stable task outcome.
    The maintained command name and verbosity remain fixed.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If command or result changes.
    """
    with patch("config.tasks.call_command") as call_command:
        result = run_flush_expired_jwt_tokens()

    call_command.assert_called_once_with("flushexpiredtokens", verbosity=0)
    assert result == {"flushed": 1}


@pytest.mark.unit
def test_account_token_cleanup_reports_overlap_without_querying_models() -> None:
    """Skip bounded retention when another invocation owns the advisory lock.

    Returns a false lock result from the external database cursor and verifies the public task
    reports one visible skip with no model access.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If overlap performs deletion or hides the skip.
    """
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = (False,)

    with patch("config.tasks.connections", {"default": connection}):
        result = run_cleanup_expired_account_tokens()

    assert result == {
        "activation": 0,
        "password_reset": 0,
        "skipped": 1,
        "username_reset": 0,
    }


@pytest.mark.unit
@freeze_time("2026-09-21 12:00:00+00:00")
def test_account_token_cleanup_deletes_one_bounded_batch_per_protocol() -> None:
    """Delete oldest primary records for all three credential protocols.

    Substitutes the external database cursor and model querysets while retaining task cutoffs,
    batch bounds, lock lifecycle, and outcome aggregation.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If a protocol is skipped or the task reports another count.
    """
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = (True,)
    activation = CleanupQueryDouble(list(range(ACTIVATION_DELETIONS)))
    password = CleanupQueryDouble(list(range(PASSWORD_DELETIONS)))
    username = CleanupQueryDouble(list(range(USERNAME_DELETIONS)))

    with (
        patch("config.tasks.connections", {"default": connection}),
        patch.object(ActivationToken, "objects", activation),
        patch.object(PasswordResetToken, "objects", password),
        patch.object(UsernameResetToken, "objects", username),
    ):
        result = run_cleanup_expired_account_tokens()

    assert result == {
        "activation": ACTIVATION_DELETIONS,
        "password_reset": PASSWORD_DELETIONS,
        "skipped": 0,
        "username_reset": USERNAME_DELETIONS,
    }
    expected_cutoffs = (
        timezone.now()
        - timedelta(
            seconds=settings.ACCOUNT_ACTIVATION_TOKEN_LIFETIME_SECONDS
            + TOKEN_TIMESTAMP_PRECISION_CUSHION_SECONDS
        ),
        timezone.now()
        - timedelta(
            seconds=settings.PASSWORD_RESET_TIMEOUT + TOKEN_TIMESTAMP_PRECISION_CUSHION_SECONDS
        ),
        timezone.now()
        - timedelta(
            seconds=settings.USERNAME_RESET_TIMEOUT + TOKEN_TIMESTAMP_PRECISION_CUSHION_SECONDS
        ),
    )
    for query, cutoff in zip(
        (activation, password, username),
        expected_cutoffs,
        strict=True,
    ):
        assert query.predicates[0] == {"issued_at__lte": cutoff}


@pytest.mark.unit
@pytest.mark.parametrize("delay", [-1.0, float("inf"), float("nan")])
def test_slow_worker_probe_rejects_unbounded_delays(delay: float) -> None:
    """Refuse delays that could evade the worker's bounded runtime.

    Supplies invalid finite and non-finite values before any sleep or broker metadata is used.
    Each rejection must happen synchronously.

    Arguments:
        delay: Invalid duration to reject.

    Returns:
        None.

    Raises:
        ValueError: Expected for every invalid duration.
    """
    task = cast("Task", SimpleNamespace(request=SimpleNamespace(delivery_info={})))

    with pytest.raises(ValueError, match="within the task soft limit"):
        run_slow_worker_probe(task, "unit-probe", delay)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("redelivered", "expected_sleep_count"),
    [(False, 1), (True, 0)],
)
def test_slow_worker_probe_delays_only_the_first_delivery(
    *,
    redelivered: bool,
    expected_sleep_count: int,
) -> None:
    """Sleep for valid original work and complete redelivery immediately.

    Supplies broker delivery metadata through the bound task seam and observes the external clock
    call without starting a worker.

    Arguments:
        redelivered: Whether RabbitMQ marked the message as redelivered.
        expected_sleep_count: Number of sleeps the task should request.

    Returns:
        None.

    Raises:
        AssertionError: If valid or redelivered behavior changes.
    """
    task = cast(
        "Task",
        SimpleNamespace(request=SimpleNamespace(delivery_info={"redelivered": redelivered})),
    )

    with patch("config.tasks.time.sleep") as sleep:
        result = run_slow_worker_probe(task, "unit-probe", 0.25)

    assert result == "unit-probe"
    assert sleep.call_count == expected_sleep_count
    if expected_sleep_count:
        sleep.assert_called_once_with(0.25)
