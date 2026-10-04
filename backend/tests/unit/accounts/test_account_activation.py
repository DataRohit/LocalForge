"""Unit tests for activation request validation.

Exercises exact field contracts, normalized resend addresses, and confirmation serialization
without issuing database state or dispatching email.
"""

import uuid
from contextlib import nullcontext
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast
from unittest.mock import MagicMock, patch

import pytest
from django.db import DatabaseError
from django.utils import timezone
from freezegun import freeze_time
from rest_framework.exceptions import ValidationError

from accounts.account_activation import (
    ActivationConfirmationSerializer,
    ActivationResendSerializer,
    ActivationResendThrottle,
    confirm_activation,
    issue_activation,
    request_activation_resend,
    resolve_activation_account,
    save_account_from_admin,
)
from accounts.activation_tokens import create_activation_token
from accounts.login_throttle import ThrottleDecision
from accounts.models import ActivationToken, User
from config.api_errors import (
    ActivationTokenExpired,
    ActivationTokenForeign,
    ActivationTokenMalformed,
    ActivationTokenUsed,
    ServiceUnavailable,
)
from tests.factories import build_activation_token, build_user

if TYPE_CHECKING:
    from rest_framework.request import Request
    from rest_framework.views import APIView

RETRY_AFTER_SECONDS = 17
DIGEST_HEX_LENGTH = 64


@pytest.mark.unit
def test_activation_confirmation_accepts_only_account_and_token() -> None:
    """Validate one complete activation confirmation shape.

    Compares the native account identifier and untrimmed token while proving an undeclared field
    receives the exact strict-field error.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If valid data changes or an extra field is ignored.
    """
    account_id = uuid.uuid4()
    serializer = ActivationConfirmationSerializer(
        data={"account": str(account_id), "token": " token "}
    )

    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data == {"account": account_id, "token": " token "}

    invalid = ActivationConfirmationSerializer(
        data={"account": str(account_id), "token": "token", "extra": "value"}
    )
    with pytest.raises(ValidationError) as failure:
        invalid.is_valid(raise_exception=True)

    assert failure.value.detail == {"extra": ["This field is not allowed."]}


@pytest.mark.unit
@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (
            {},
            {
                "account": ["This field is required."],
                "token": ["This field is required."],
            },
        ),
        (
            {"account": "not-a-uuid", "token": "token"},
            {"account": ["Must be a valid UUID."]},
        ),
        (
            {"account": str(uuid.uuid4()), "token": ""},
            {"token": ["This field may not be blank."]},
        ),
    ],
)
def test_activation_confirmation_returns_exact_field_errors(
    data: dict[str, object],
    expected: dict[str, list[str]],
) -> None:
    """Return stable missing, malformed, and blank confirmation details.

    Calls the public serializer and compares the complete field-keyed error mapping.
    Every declared field remains required and type-checked.

    Arguments:
        data: Invalid confirmation body.
        expected: Exact validation detail.

    Returns:
        None.

    Raises:
        AssertionError: If any field error changes.
    """
    serializer = ActivationConfirmationSerializer(data=data)

    assert serializer.is_valid() is False
    assert serializer.errors == expected


@pytest.mark.unit
def test_activation_resend_normalizes_the_address() -> None:
    """Canonicalize the sole resend identity.

    Supplies mixed case and verifies the serializer returns the stored representation used by
    lookup and throttle identity.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the address remains noncanonical.
    """
    serializer = ActivationResendSerializer(data={"email": "User@LOCALFORGE.Invalid"})

    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data == {"email": "user@localforge.invalid"}


@pytest.mark.unit
@pytest.mark.parametrize(
    ("data", "expected"),
    [
        ({}, {"email": ["This field is required."]}),
        ({"email": "not-an-email"}, {"email": ["Enter a valid email address."]}),
    ],
)
def test_activation_resend_returns_exact_email_errors(
    data: dict[str, object],
    expected: dict[str, list[str]],
) -> None:
    """Return stable missing and malformed address detail.

    Calls the public resend serializer and compares the complete field-keyed error shape.
    Both invalid classes remain independent of account existence.

    Arguments:
        data: Invalid resend body.
        expected: Exact validation detail.

    Returns:
        None.

    Raises:
        AssertionError: If required or format errors change shape.
    """
    serializer = ActivationResendSerializer(data=data)

    assert serializer.is_valid() is False
    assert serializer.errors == expected


@pytest.mark.unit
@pytest.mark.parametrize(
    "decision",
    [
        ThrottleDecision(admitted=True, retry_after_seconds=RETRY_AFTER_SECONDS),
        ThrottleDecision(admitted=False, retry_after_seconds=RETRY_AFTER_SECONDS),
    ],
)
def test_activation_resend_throttle_returns_the_authoritative_decision(
    decision: ThrottleDecision,
) -> None:
    """Expose both allow and deny outcomes from the primary-backed throttle.

    Substitutes transaction, account resolution, and admission storage while retaining request
    validation, rule construction, result propagation, and retry delay.

    Arguments:
        decision: Authoritative admission outcome to propagate.

    Returns:
        None.

    Raises:
        AssertionError: If allow, deny, or wait behavior differs.
    """
    request = cast(
        "Request",
        SimpleNamespace(
            method="POST",
            data={"email": "User@LOCALFORGE.Invalid"},
            META={"REMOTE_ADDR": "192.0.2.10", "request_id": "request-id"},
        ),
    )
    throttle = ActivationResendThrottle()
    connection = MagicMock()
    connection.cursor.return_value.__enter__.return_value.fetchone.return_value = (
        "user@localforge.invalid",
    )
    accounts = MagicMock()
    account_query = accounts.using.return_value.select_for_update.return_value.alias.return_value
    account_query.get.side_effect = User.DoesNotExist

    with (
        patch("accounts.account_activation.transaction.atomic", return_value=nullcontext()),
        patch("accounts.account_activation.connections", {"default": connection}),
        patch.object(User, "objects", accounts),
        patch(
            "accounts.account_activation.PostgresLoginThrottleStore.admit",
            return_value=decision,
        ),
    ):
        result = throttle.allow_request(request, cast("APIView", object()))

    assert result is decision.admitted
    assert throttle.wait() == float(RETRY_AFTER_SECONDS)


@pytest.mark.unit
def test_activation_account_resolver_returns_match_or_none() -> None:
    """Resolve primary account state through PostgreSQL semantics.

    Substitutes the ORM chain and verifies both matching and missing outcomes through the public
    helper.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If lookup result or missing classification differs.
    """
    account = build_user()
    accounts = MagicMock()
    query = accounts.using.return_value.select_for_update.return_value.alias.return_value
    query.get.return_value = account

    with patch.object(User, "objects", accounts):
        assert resolve_activation_account(account.email) is account
        query.get.side_effect = User.DoesNotExist
        assert resolve_activation_account(account.email) is None


@pytest.mark.unit
def test_issue_activation_persists_digest_and_registers_after_commit() -> None:
    """Issue one activation record through public orchestration.

    Substitutes token persistence and transaction callback registration while retaining real token
    generation and digesting.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If persistence or after-commit publication is omitted.
    """
    account = build_user()
    records = MagicMock()

    with (
        patch.object(ActivationToken, "objects", records),
        patch("accounts.account_activation.transaction.on_commit") as on_commit,
    ):
        issue_activation(account)

    create = records.using.return_value.create
    create.assert_called_once()
    assert create.call_args.kwargs["account"] is account
    assert create.call_args.kwargs["subject_id"] == account.pk
    assert len(create.call_args.kwargs["digest"]) == DIGEST_HEX_LENGTH
    on_commit.assert_called_once()


@pytest.mark.unit
def test_activation_resend_schedules_dummy_work_for_an_active_account() -> None:
    """Keep active-account resend indistinguishable without issuing state.

    Returns one active account from the external ORM and verifies the real dummy scheduling path
    registers after commit.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If active accounts receive a real record or no task-shaped work.
    """
    account = build_user(is_active=True)
    accounts = MagicMock()
    account_query = accounts.using.return_value.select_for_update.return_value.alias.return_value
    account_query.get.return_value = account

    with (
        patch.object(User, "objects", accounts),
        patch("accounts.account_activation.transaction.atomic", return_value=nullcontext()),
        patch("accounts.account_activation.transaction.on_commit") as on_commit,
    ):
        request_activation_resend(account.email)

    on_commit.assert_called_once()


@pytest.mark.unit
def test_activation_confirmation_classifies_malformed_and_foreign_tokens() -> None:
    """Reject malformed and mismatched bearers before database state.

    Calls public confirmation with invalid text and a valid token bound to another immutable
    account, comparing the exact protocol exceptions.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If either bearer class receives another exception.
    """
    account_id = uuid.uuid4()
    with pytest.raises(ActivationTokenMalformed):
        confirm_activation({"account": account_id, "token": "not-a-token"})

    foreign = create_activation_token(str(uuid.uuid4()))
    with pytest.raises(ActivationTokenForeign):
        confirm_activation({"account": account_id, "token": foreign})


@pytest.mark.unit
def test_activation_confirmation_classifies_expired_token() -> None:
    """Reject a valid activation bearer after its configured lifetime.

    Freezes issuance and confirmation two days apart so expiry classification is deterministic and
    occurs before database state.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        ActivationTokenExpired: Expected after the configured lifetime.
    """
    account_id = uuid.uuid4()
    with freeze_time("2026-09-21 00:00:00+00:00"):
        token = create_activation_token(str(account_id))
    with (
        freeze_time("2026-09-23 00:00:00+00:00"),
        pytest.raises(ActivationTokenExpired),
    ):
        confirm_activation({"account": account_id, "token": token})


@pytest.mark.unit
@pytest.mark.parametrize("state", ["unused", "used"])
def test_activation_confirmation_applies_or_rejects_locked_token_state(state: str) -> None:
    """Activate one unused account and reject one consumed token.

    Substitutes primary account and token querysets while retaining payload authentication, digest
    lookup, lock ordering, account mutation, and outstanding-token consumption.

    Arguments:
        state: Whether the authoritative token is unused or consumed.

    Returns:
        None.

    Raises:
        ActivationTokenUsed: Expected for consumed state.
        AssertionError: If unused activation does not persist.
    """
    used = state == "used"
    account = build_user(is_active=False)
    token = create_activation_token(str(account.pk))
    record = build_activation_token(subject_id=account.pk)
    record.pk = 7
    record.account = account
    record.used_at = timezone.now() if used else None
    records = MagicMock()
    token_query = records.using.return_value
    token_query.filter.return_value.values_list.return_value.first.return_value = record.pk
    token_query.select_for_update.return_value.filter.return_value.first.return_value = record
    accounts = MagicMock()
    accounts.using.return_value.select_for_update.return_value.get.return_value = account

    with (
        patch.object(User, "objects", accounts),
        patch.object(ActivationToken, "objects", records),
        patch.object(account, "save") as save,
        patch("accounts.account_activation.transaction.atomic", return_value=nullcontext()),
    ):
        if used:
            with pytest.raises(ActivationTokenUsed):
                confirm_activation({"account": account.pk, "token": token})
        else:
            confirm_activation({"account": account.pk, "token": token})

    if used:
        save.assert_not_called()
    else:
        assert account.is_active is True
        save.assert_called_once()
        token_query.filter.return_value.update.assert_called_once()


@pytest.mark.unit
def test_activation_confirmation_maps_database_failure_to_service_unavailable() -> None:
    """Contain authoritative lookup failure at the public operation.

    Supplies a matching preflight token then raises from the primary account lookup, verifying the
    stable dependency exception.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        ServiceUnavailable: Expected for database failure.
    """
    account_id = uuid.uuid4()
    token = create_activation_token(str(account_id))
    records = MagicMock()
    records.using.return_value.filter.return_value.values_list.return_value.first.return_value = 7
    accounts = MagicMock()
    accounts.using.return_value.select_for_update.return_value.get.side_effect = DatabaseError

    with (
        patch.object(User, "objects", accounts),
        patch.object(ActivationToken, "objects", records),
        patch("accounts.account_activation.transaction.atomic", return_value=nullcontext()),
        pytest.raises(ServiceUnavailable),
    ):
        confirm_activation({"account": account_id, "token": token})


@pytest.mark.unit
def test_admin_save_consumes_links_before_inactive_email_change() -> None:
    """Persist one inactive email change through activation-aware orchestration.

    Substitutes primary account and token persistence while retaining normalization, transition
    detection, token consumption, and supported save fields.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If email change fails to consume links or persist.
    """
    persisted = build_user(email="old@localforge.invalid", is_active=False)
    account = build_user(email=" NEW@LOCALFORGE.Invalid ", is_active=False)
    account.pk = persisted.pk
    accounts = MagicMock()
    accounts.using.return_value.select_for_update.return_value.get.return_value = persisted
    records = MagicMock()

    with (
        patch.object(User, "objects", accounts),
        patch.object(ActivationToken, "objects", records),
        patch.object(account, "save") as save,
        patch("accounts.account_activation.transaction.atomic", return_value=nullcontext()),
    ):
        save_account_from_admin(account)

    assert account.email == "new@localforge.invalid"
    records.using.return_value.filter.return_value.update.assert_called_once()
    save.assert_called_once_with(using="default")
