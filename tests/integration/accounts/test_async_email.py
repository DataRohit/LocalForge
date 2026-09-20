"""Integration tests for retry-safe account notification tasks.

Exercises credential-free password and username change notifications through the Celery task seam,
including identifier-only payloads, bounded retry, permanent failure, and deleted-account no-op.
"""

from __future__ import annotations

import secrets
import uuid
from typing import TYPE_CHECKING, cast

import pytest
from django.core import mail

import accounts.tasks as account_tasks
import config.celery as celery_module
from accounts.models import User
from accounts.tasks import (
    send_activation_email,
    send_password_changed_email,
    send_password_reset_email,
    send_username_changed_email,
    send_username_reset_email,
)

if TYPE_CHECKING:
    from celery.result import EagerResult

EAGER_FAILURES_CAPTURED = False


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_credential_free_notification_tasks_resolve_only_the_account_identifier() -> None:
    """Send both change notices from one immutable account identifier.

    Calls the public tasks directly under the eager testing seam and requires current primary
    account state to supply the recipient, with no email address or user object in the task call.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If either notice is missing or task payload requires account data.
    """
    account = User.objects.db_manager("default").create_user(
        f"async-notice-{uuid.uuid4().hex}",
        f"async-notice-{uuid.uuid4().hex}@localforge.invalid",
        secrets.token_urlsafe(24),
        is_active=True,
    )

    password_delivered = send_password_changed_email(str(account.pk))
    username_delivered = send_username_changed_email(str(account.pk))

    assert password_delivered is True
    assert username_delivered is True
    assert [message.subject for message in mail.outbox] == [
        "Your LocalForge password changed",
        "Your LocalForge username changed",
    ]


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_deleted_account_notification_is_an_idempotent_no_op() -> None:
    """Suppress a redelivered notification after its account disappeared.

    Deletes the account before execution and requires a successful no-op, preventing futile retries
    or a terminal failure for a recipient that no longer exists.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If deletion causes email, retry, or task failure.
    """
    account_id = str(uuid.uuid4())

    assert send_password_changed_email(account_id) is True
    assert send_username_changed_email(account_id) is True
    assert account_tasks.deliver_password_changed_email(object()) is True
    assert account_tasks.deliver_username_changed_email(object()) is True
    assert not mail.outbox


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_transient_notification_failure_retries_and_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retry one credential-free notification after a transient rejection.

    Makes the first delivery return false and the second succeed, requiring Celery's eager retry
    loop to complete without a dead-letter record.

    Arguments:
        monkeypatch: Fixture controlling the mail boundary and terminal publisher.

    Returns:
        None.

    Raises:
        AssertionError: If the task does not retry exactly once or reaches terminal failure.
    """
    account = User.objects.db_manager("default").create_user(
        f"async-retry-{uuid.uuid4().hex}",
        f"async-retry-{uuid.uuid4().hex}@localforge.invalid",
        secrets.token_urlsafe(24),
        is_active=True,
    )
    attempts: list[bool] = []
    outcomes = iter((False, True))

    def deliver(*_arguments: object, **_keywords: object) -> bool:
        """Return one configured mail outcome and record the attempt.

        Replaces the external mail boundary with one transient rejection followed by acceptance,
        leaving Celery's retry lifecycle intact.

        Arguments:
            *_arguments: Positional mail fields ignored by this boundary.
            **_keywords: Optional mail fields ignored by this boundary.

        Returns:
            Next configured delivery outcome.
        """
        outcome = next(outcomes)
        attempts.append(outcome)
        return outcome

    dead_letters: list[celery_module.DeadLetterRecord] = []
    monkeypatch.setattr(account_tasks, "send_application_email", deliver)
    monkeypatch.setattr(celery_module, "publish_dead_letter", dead_letters.append)

    result = send_password_changed_email.apply(args=(str(account.pk),), throw=False)

    assert result.successful()
    assert result.result is True
    assert attempts == [False, True]
    assert not dead_letters


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_permanent_notification_failure_stops_at_the_bound_and_deadletters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stop retrying a credential-free notification after the configured maximum.

    Rejects every send attempt and requires one terminal scrubbed record after the initial attempt
    plus the base task's bounded retries.

    Arguments:
        monkeypatch: Fixture controlling the mail boundary and terminal publisher.

    Returns:
        None.

    Raises:
        AssertionError: If retries are unbounded, terminal state is absent, or payload data leaks.
    """
    account = User.objects.db_manager("default").create_user(
        f"async-failure-{uuid.uuid4().hex}",
        f"async-failure-{uuid.uuid4().hex}@localforge.invalid",
        secrets.token_urlsafe(24),
        is_active=True,
    )
    attempts = 0

    def reject(*_arguments: object, **_keywords: object) -> bool:
        """Reject one mail attempt.

        Replaces the external mail boundary with a stable permanent rejection while preserving the
        task's real retry and terminal hooks.

        Arguments:
            *_arguments: Positional mail fields ignored by this boundary.
            **_keywords: Optional mail fields ignored by this boundary.

        Returns:
            False, always.
        """
        nonlocal attempts
        attempts += 1
        return False

    dead_letters: list[celery_module.DeadLetterRecord] = []
    monkeypatch.setattr(account_tasks, "send_application_email", reject)
    monkeypatch.setattr(celery_module, "publish_dead_letter", dead_letters.append)
    monkeypatch.setitem(
        send_username_changed_email.app.conf,
        "CELERY_TASK_EAGER_PROPAGATES",
        EAGER_FAILURES_CAPTURED,
    )

    result = cast(
        "EagerResult",
        send_username_changed_email.apply_async(args=(str(account.pk),)),
    )

    assert result.failed()
    assert attempts == celery_module.MAX_RETRIES + 1
    assert len(dead_letters) == 1
    assert dead_letters[0].task_name == "accounts.send_username_changed_email"
    assert dead_letters[0].task_args == ["str"]
    assert dead_letters[0].task_kwargs == {}


@pytest.mark.integration
@pytest.mark.services("valkey-cache")
def test_credential_bearing_email_tasks_preserve_their_no_retry_contract() -> None:
    """Keep bearer delivery at most once while notifications remain retry-safe.

    Confirms the three completed link-delivery tasks still override automatic retries after the
    Ticket 45 source correction.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If any credential-bearing task can retry a claimed bearer.
    """
    assert send_activation_email.autoretry_for == ()
    assert send_password_reset_email.autoretry_for == ()
    assert send_username_reset_email.autoretry_for == ()
