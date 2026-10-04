"""Unit tests for account email task functions.

Exercises invalid/deleted-account no-op behavior, backend rejection, successful notification
publication, and publication-failure containment separately from Celery execution.
"""

from __future__ import annotations

import logging
import uuid
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, cast
from unittest.mock import Mock, patch

import pytest
from freezegun import freeze_time

from accounts import tasks
from accounts.activation_tokens import activation_token_digest, create_activation_token
from accounts.models import (
    ActivationToken,
    PasswordResetToken,
    User,
    UsernameResetToken,
)
from accounts.password_tokens import create_password_reset_token, password_reset_token_digest
from accounts.tasks import (
    AccountEmailDeliveryError,
    deliver_activation_email,
    deliver_password_changed_email,
    deliver_password_reset_email,
    deliver_username_changed_email,
    deliver_username_reset_email,
)
from accounts.username_tokens import create_username_reset_token, username_reset_token_digest
from tests.factories import (
    build_activation_token,
    build_password_reset_token,
    build_user,
    build_username_reset_token,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    TokenRecord = ActivationToken | PasswordResetToken | UsernameResetToken
    CredentialDelivery = Callable[[object, object], bool]


class AccountQueryDouble:
    """Substitute authoritative account lookup.

    Inherits nothing and implements the manager/queryset methods account task functions call,
    returning either the configured account or its current email.

    Attributes:
        account: Account returned by lookup, or None for deletion.
        values_only: Whether the query requested only the email field.

    Members:
        using: Select the authoritative alias.
        select_for_update: Preserve the fluent locking query.
        values_list: Select current email output.
        get: Return the configured result or raise missing-account state.
    """

    def __init__(self, account: User | None, *, values_only: bool = False) -> None:
        """Store one account lookup result.

        Keeps value-selection state on a new query double so one test cannot leak it into another
        task call.

        Arguments:
            account: Account result, or None to simulate deletion.
            values_only: Whether get returns only the address.

        Returns:
            None.
        """
        self.account = account
        self.values_only = values_only

    def using(self, alias: str) -> AccountQueryDouble:
        """Select the primary alias.

        Requires every task lookup to name the authoritative database explicitly.
        No connection is opened by the test double.

        Arguments:
            alias: Database alias selected by task code.

        Returns:
            This fluent query double.

        Raises:
            AssertionError: If another alias is selected.
        """
        assert alias == "default"
        return self

    def select_for_update(self) -> AccountQueryDouble:
        """Preserve the account-locking query shape.

        Returns the same double because locking is an integration concern.
        Task code must still request the lock explicitly.

        Arguments:
            None.

        Returns:
            This fluent query double.
        """
        return self

    def values_list(self, field: str, *, flat: bool) -> AccountQueryDouble:
        """Select the current email address.

        Returns a new value-only query after checking the exact field and flat shape.
        The original account query remains unchanged.

        Arguments:
            field: Model field selected by task code.
            flat: Whether one scalar value is requested.

        Returns:
            Value-only query double.

        Raises:
            AssertionError: If another query shape is selected.
        """
        assert field == "email"
        assert flat is True
        return AccountQueryDouble(self.account, values_only=True)

    def get(self, **_lookup: object) -> User | str:
        """Return the configured account or email.

        Raises the model's public missing exception when deletion is simulated.
        Lookup values remain owned by the task implementation.

        Arguments:
            **_lookup: Lookup values ignored after query-shape assertions.

        Returns:
            Account or current address.

        Raises:
            User.DoesNotExist: If no live account remains.
        """
        if self.account is None:
            raise User.DoesNotExist
        return str(self.account.email) if self.values_only else self.account


class TokenQueryDouble:
    """Substitute one credential-state lookup.

    Inherits nothing and retains the fluent manager/queryset operations task delivery uses before
    returning the configured token record.

    Attributes:
        record: Token record to return, or None for an unavailable claim.

    Members:
        using: Select the authoritative alias.
        select_for_update: Preserve the locking query.
        filter: Accept task-owned classification filters.
        first: Return the configured record.
    """

    def __init__(self, record: TokenRecord | None) -> None:
        """Store one token query result.

        Keeps one deterministic record outcome for every fluent query operation.
        No token state is persisted by the double.

        Arguments:
            record: Record to return, or None to simulate missing/unclaimable state.

        Returns:
            None.
        """
        self.record = record

    def using(self, alias: str) -> TokenQueryDouble:
        """Select the primary alias.

        Requires task code to name the authoritative database explicitly.
        No connection is opened by the double.

        Arguments:
            alias: Database alias selected by task code.

        Returns:
            This fluent query double.

        Raises:
            AssertionError: If another alias is selected.
        """
        assert alias == "default"
        return self

    def select_for_update(self) -> TokenQueryDouble:
        """Preserve the token-locking query shape.

        Returns the same double while retaining evidence that task code requested locking.
        Actual lock semantics remain integration-owned.

        Arguments:
            None.

        Returns:
            This fluent query double.
        """
        return self

    def filter(self, **_lookup: object) -> TokenQueryDouble:
        """Accept the task's authoritative token classification filters.

        Retains the fluent public queryset shape without evaluating database predicates.
        Integration tests own predicate enforcement.

        Arguments:
            **_lookup: Token state predicates applied by task code.

        Returns:
            This fluent query double.
        """
        return self

    def first(self) -> TokenRecord | None:
        """Return the configured token state.

        Supplies one record or no claim after task-owned classification is complete.
        No fallback record is synthesized.

        Arguments:
            None.

        Returns:
            Token record or None.
        """
        return self.record


@dataclass(frozen=True, slots=True)
class CredentialCase:
    """Describe one credential-email protocol case.

    Inherits from ``dataclass`` and bundles the account, bearer, persisted record, model class, and
    public task function that change together across activation, password reset, and username reset.

    Attributes:
        account: Current authoritative account state.
        token: Issued bearer value.
        record: Digest-only token record.
        model: Record model whose manager task code queries.
        deliver: Public task function under test.

    Members:
        None.
    """

    account: User
    token: str
    record: TokenRecord
    model: type[TokenRecord]
    deliver: CredentialDelivery


def credential_case(kind: Literal["activation", "password", "username"]) -> CredentialCase:
    """Build one valid credential-email protocol case.

    Uses real token primitives and valid factories so task validation and matching remain active
    while database and SMTP are substituted at their external boundaries.

    Arguments:
        kind: Credential protocol to build.

    Returns:
        Complete protocol case.
    """
    account = build_user(is_active=kind != "activation")
    record: TokenRecord
    model: type[TokenRecord]
    deliver: CredentialDelivery
    if kind == "activation":
        token = create_activation_token(str(account.pk))
        record = build_activation_token(
            subject_id=account.pk,
            digest=activation_token_digest(token),
        )
        model = ActivationToken
        deliver = cast("CredentialDelivery", deliver_activation_email)
    elif kind == "password":
        token = create_password_reset_token(account)
        record = build_password_reset_token(
            subject_id=account.pk,
            digest=password_reset_token_digest(token),
        )
        model = PasswordResetToken
        deliver = deliver_password_reset_email
    else:
        token = create_username_reset_token(account)
        record = build_username_reset_token(
            subject_id=account.pk,
            digest=username_reset_token_digest(token),
        )
        model = UsernameResetToken
        deliver = deliver_username_reset_email
    record.pk = 7

    return CredentialCase(
        account=account,
        token=token,
        record=record,
        model=model,
        deliver=deliver,
    )


@contextmanager
def delivery_boundaries(
    case: CredentialCase,
    *,
    record: TokenRecord | None,
    accepted: bool,
) -> Iterator[Mock]:
    """Build reversible external-boundary patches for one task case.

    Substitutes primary account/token queries, transaction management, record persistence, and SMTP
    while leaving the complete public task implementation active.

    Arguments:
        case: Credential protocol case under test.
        record: Token query result, or None for an unavailable claim.
        accepted: SMTP acceptance result.

    Yields:
        SMTP mock observing the attempted delivery.
    """
    with (
        patch.object(User, "objects", AccountQueryDouble(case.account)),
        patch.object(case.model, "objects", TokenQueryDouble(record)),
        patch("accounts.tasks.transaction.atomic", return_value=nullcontext()),
        patch("accounts.tasks.send_application_email", return_value=accepted) as email,
        patch.object(case.record, "save"),
    ):
        yield email


@pytest.mark.unit
@pytest.mark.parametrize("account_id", [None, 7, "", "not-a-uuid"])
def test_notification_tasks_treat_invalid_or_missing_accounts_as_no_op(
    account_id: object,
) -> None:
    """Complete safely when queued identity can no longer resolve.

    Uses values rejected before ORM access so no service boundary is reached.
    Deleted-account behavior shares the same successful outcome.

    Arguments:
        account_id: Invalid immutable account candidate.

    Returns:
        None.

    Raises:
        AssertionError: If invalid identity fails or reaches delivery.
    """
    assert deliver_password_changed_email(account_id) is True
    assert deliver_username_changed_email(account_id) is True


@pytest.mark.unit
@pytest.mark.parametrize(
    ("task_function", "arguments"),
    [
        (deliver_activation_email, ("not-a-uuid", "not-a-token")),
        (deliver_password_reset_email, (None, "token")),
        (deliver_password_reset_email, (str(uuid.uuid4()), None)),
        (deliver_username_reset_email, (None, "token")),
        (deliver_username_reset_email, (str(uuid.uuid4()), None)),
    ],
)
def test_credential_email_tasks_reject_invalid_payloads_before_state_access(
    task_function: Callable[..., bool],
    arguments: tuple[object, ...],
) -> None:
    """Reject malformed credential-task payloads directly.

    Calls each public task function without Celery and requires failure before ORM or SMTP access.
    The result remains the task contract's explicit false outcome.

    Arguments:
        task_function: Credential-delivery function to exercise.
        arguments: Invalid queue payload values.

    Returns:
        None.

    Raises:
        AssertionError: If malformed payload is accepted or reaches a service.
    """
    assert task_function(*arguments) is False


@pytest.mark.unit
@pytest.mark.parametrize("kind", ["activation", "password", "username"])
@freeze_time("2026-09-21 00:00:00+00:00")
def test_credential_email_tasks_deliver_one_valid_claim(
    kind: Literal["activation", "password", "username"],
) -> None:
    """Deliver one valid claimed message through each credential protocol.

    Uses real token parsing and matching with substituted database, transaction, persistence, and
    SMTP boundaries.

    Arguments:
        kind: Credential protocol to exercise.

    Returns:
        None.

    Raises:
        AssertionError: If valid delivery or email invocation fails.
    """
    case = credential_case(kind)

    with delivery_boundaries(case, record=case.record, accepted=True) as email:
        delivered = case.deliver(str(case.account.pk), case.token)

    assert delivered is True
    email.assert_called_once()


@pytest.mark.unit
@pytest.mark.parametrize("kind", ["activation", "password", "username"])
@freeze_time("2026-09-21 00:00:00+00:00")
def test_credential_email_tasks_stop_when_no_claim_is_available(
    kind: Literal["activation", "password", "username"],
) -> None:
    """Treat missing, used, or already-claimed state as no delivery.

    Returns no token record from the external persistence boundary and verifies SMTP is never
    attempted.

    Arguments:
        kind: Credential protocol to exercise.

    Returns:
        None.

    Raises:
        AssertionError: If unclaimable state sends or reports success.
    """
    case = credential_case(kind)

    with delivery_boundaries(case, record=None, accepted=True) as email:
        delivered = case.deliver(str(case.account.pk), case.token)

    assert delivered is False
    email.assert_not_called()


@pytest.mark.unit
@pytest.mark.parametrize("kind", ["activation", "password", "username"])
@freeze_time("2026-09-21 00:00:00+00:00")
def test_credential_email_tasks_contain_backend_rejection(
    kind: Literal["activation", "password", "username"],
) -> None:
    """Return false when SMTP rejects one otherwise valid claimed message.

    Keeps each protocol's no-retry public result distinct from the retry-safe notification tasks.
    The attempted SMTP call still occurs exactly once.

    Arguments:
        kind: Credential protocol to exercise.

    Returns:
        None.

    Raises:
        AssertionError: If backend rejection becomes success or skips SMTP.
    """
    case = credential_case(kind)

    with delivery_boundaries(case, record=case.record, accepted=False) as email:
        delivered = case.deliver(str(case.account.pk), case.token)

    assert delivered is False
    email.assert_called_once()


@pytest.mark.unit
@pytest.mark.parametrize("kind", ["activation", "password", "username"])
@freeze_time("2026-09-21 00:00:00+00:00")
def test_credential_email_tasks_reject_mismatched_account_state(
    kind: Literal["activation", "password", "username"],
) -> None:
    """Reject a bearer issued for another immutable account.

    Uses real signature and account-state matching while substituting only persistence and SMTP.
    No mismatched credential may reach the delivery boundary.

    Arguments:
        kind: Credential protocol to exercise.

    Returns:
        None.

    Raises:
        AssertionError: If a foreign bearer reaches delivery.
    """
    case = credential_case(kind)
    other = build_user(is_active=kind != "activation")
    if kind == "activation":
        foreign_token = create_activation_token(str(other.pk))
        assert case.deliver(str(case.account.pk), foreign_token) is False
        return
    if kind == "password":
        foreign_token = create_password_reset_token(other)
        case.record.digest = password_reset_token_digest(foreign_token)
    else:
        foreign_token = create_username_reset_token(other)
        case.record.digest = username_reset_token_digest(foreign_token)

    with delivery_boundaries(case, record=case.record, accepted=True) as email:
        delivered = case.deliver(str(case.account.pk), foreign_token)

    assert delivered is False
    email.assert_not_called()


@pytest.mark.unit
@pytest.mark.parametrize("kind", ["activation", "password", "username"])
def test_credential_email_tasks_reject_expired_bearers(
    kind: Literal["activation", "password", "username"],
) -> None:
    """Reject each credential after its configured lifetime.

    Freezes issuance and advances two days so expiry is deterministic and occurs before any
    database or SMTP boundary.

    Arguments:
        kind: Credential protocol to exercise.

    Returns:
        None.

    Raises:
        AssertionError: If an expired bearer reaches state lookup.
    """
    with freeze_time("2026-09-21 00:00:00+00:00"):
        case = credential_case(kind)
    with freeze_time("2026-09-23 00:00:00+00:00"):
        delivered = case.deliver(str(case.account.pk), case.token)

    assert delivered is False


@pytest.mark.unit
def test_notification_tasks_treat_a_deleted_account_as_no_op() -> None:
    """Complete safely when a valid immutable identifier no longer resolves.

    Substitutes the external account lookup with the model's missing result and verifies neither
    notification task attempts email.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If deleted-account work fails or reaches SMTP.
    """
    with (
        patch.object(User, "objects", AccountQueryDouble(None)),
        patch("accounts.tasks.send_application_email") as email,
    ):
        account_id = str(uuid.uuid4())
        assert deliver_password_changed_email(account_id) is True
        assert deliver_username_changed_email(account_id) is True

    email.assert_not_called()


@pytest.mark.unit
def test_password_notice_raises_a_fixed_retry_signal_on_backend_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Convert one backend rejection into the credential-free task exception.

    Replaces recipient lookup and external email delivery while retaining task decision logic.
    The raised exception must carry no transport or recipient text.

    Arguments:
        monkeypatch: Fixture replacing external task boundaries.

    Returns:
        None.

    Raises:
        AccountEmailDeliveryError: Expected when the backend rejects delivery.
    """
    account = build_user(email="user@example.invalid")
    monkeypatch.setattr(User, "objects", AccountQueryDouble(account))
    monkeypatch.setattr(tasks, "send_application_email", lambda *_args, **_kwargs: False)

    with pytest.raises(AccountEmailDeliveryError) as failure:
        deliver_password_changed_email(str(account.pk))

    assert str(failure.value) == ""


@pytest.mark.unit
def test_password_notice_succeeds_after_backend_acceptance() -> None:
    """Complete one retry-safe password notice after SMTP acceptance.

    Substitutes current recipient lookup and email transport while retaining the public task
    decision and fixed message content.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If successful delivery fails or uses another message.
    """
    account = build_user(email="user@example.invalid")

    with (
        patch.object(User, "objects", AccountQueryDouble(account)),
        patch("accounts.tasks.send_application_email", return_value=True) as email,
    ):
        delivered = deliver_password_changed_email(str(account.pk))

    assert delivered is True
    email.assert_called_once_with(
        "user@example.invalid",
        "Your LocalForge password changed",
        "Your password was changed through account recovery.",
        context={
            "security_notice": ("If you did not make this change, contact support immediately."),
        },
    )


@pytest.mark.unit
def test_username_notice_raises_retry_signal_on_backend_rejection() -> None:
    """Reject one username notice when SMTP does not accept it.

    Substitutes current recipient and email transport and verifies live notification publication is
    never attempted after mail failure.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AccountEmailDeliveryError: Expected for backend rejection.
    """
    account = build_user(email="user@example.invalid")

    with (
        patch.object(User, "objects", AccountQueryDouble(account)),
        patch("accounts.tasks.send_application_email", return_value=False),
        patch("accounts.tasks.publish_notification") as publish,
        pytest.raises(AccountEmailDeliveryError),
    ):
        deliver_username_changed_email(str(account.pk))

    publish.assert_not_called()


@pytest.mark.unit
def test_username_notice_publishes_and_contains_channel_failure(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Keep completed email work successful when live publication fails.

    Captures the exact event on success, then raises unsafe channel text and verifies only the
    exception type enters the fixed failure record.

    Arguments:
        monkeypatch: Fixture replacing external task boundaries.
        caplog: Fixture capturing the safe failure record.

    Returns:
        None.

    Raises:
        AssertionError: If event shape, task success, or log redaction changes.
    """
    account = build_user(email="user@example.invalid")
    account_id = account.pk
    published: list[tuple[uuid.UUID, str, dict[str, object]]] = []
    monkeypatch.setattr(User, "objects", AccountQueryDouble(account))
    monkeypatch.setattr(tasks, "send_application_email", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        tasks,
        "publish_notification",
        lambda recipient, event, data: published.append((recipient, event, data)),
    )

    assert deliver_username_changed_email(str(account_id)) is True
    assert published == [(account_id, "account.username_changed", {})]

    marker = "unsafe-channel-detail"

    def fail_publication(*_args: object, **_kwargs: object) -> None:
        """Raise one unsafe channel-layer failure.

        Supplies marker text that task logging must not retain.
        The failure occurs only after successful email delivery.

        Arguments:
            *_args: Ignored publication values.
            **_kwargs: Ignored publication fields.

        Returns:
            Never returns.

        Raises:
            RuntimeError: Always.
        """
        raise RuntimeError(marker)

    monkeypatch.setattr(tasks, "publish_notification", fail_publication)
    with caplog.at_level(logging.ERROR, logger=tasks.__name__):
        assert deliver_username_changed_email(str(account_id)) is True

    assert caplog.records[-1].getMessage() == "Username notification publication failed"
    assert vars(caplog.records[-1])["notification_error_type"] == "RuntimeError"
    assert marker not in caplog.text
