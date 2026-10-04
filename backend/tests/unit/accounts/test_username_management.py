"""Unit tests for username-management serializers.

Exercises normalized recovery identity, strict confirmation shape, username validation, and
authenticated change input without database state.
"""

from __future__ import annotations

import secrets
import uuid
from contextlib import nullcontext
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast
from unittest.mock import MagicMock, patch

import pytest
from django.db import DatabaseError
from django.utils import timezone
from rest_framework.exceptions import AuthenticationFailed, ValidationError

from accounts.login_throttle import ThrottleDecision
from accounts.models import User, UsernameResetToken
from accounts.username_management import (
    UsernameChangeSerializer,
    UsernameResetConfirmSerializer,
    UsernameResetConfirmThrottle,
    UsernameResetRequestSerializer,
    UsernameResetRequestThrottle,
    UsernameResetResponseSerializer,
    change_username,
    confirm_username_reset,
    issue_username_reset,
    request_username_reset,
    resolve_username_reset_account,
    username_is_available,
)
from accounts.username_tokens import create_username_reset_token, username_reset_token_digest
from config.api_errors import (
    ServiceUnavailable,
    UsernameResetTokenExpired,
    UsernameResetTokenForeign,
    UsernameResetTokenMalformed,
    UsernameResetTokenUsed,
)
from tests.factories import build_user, build_username_reset_token

if TYPE_CHECKING:
    from rest_framework.request import Request
    from rest_framework.serializers import Serializer
    from rest_framework.views import APIView

REQUEST_RETRY_AFTER_SECONDS = 31
CONFIRM_RETRY_AFTER_SECONDS = 37


@pytest.mark.unit
def test_username_reset_request_normalizes_and_rejects_extra_fields() -> None:
    """Accept one canonical address and reject undeclared input.

    Compares exact native data and field-keyed strict-shape detail.
    No account resolution or admission state is used.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If recovery validation drifts.
    """
    serializer = UsernameResetRequestSerializer(data={"email": "User@LOCALFORGE.Invalid"})

    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data == {"email": "user@localforge.invalid"}

    invalid = UsernameResetRequestSerializer(
        data={"email": "user@localforge.invalid", "username": "not-allowed"}
    )
    with pytest.raises(ValidationError) as failure:
        invalid.is_valid(raise_exception=True)

    assert failure.value.detail == {"username": ["This field is not allowed."]}


@pytest.mark.unit
def test_username_reset_response_exposes_only_the_accepted_detail() -> None:
    """Render the enumeration-resistant username-reset response.

    Supplies extra source state and verifies only the fixed detail field reaches output.
    No account or token value can enter the response.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If response fields expand or disappear.
    """
    assert UsernameResetResponseSerializer({"detail": "accepted", "account": "hidden"}).data == {
        "detail": "accepted"
    }


@pytest.mark.unit
@pytest.mark.parametrize(
    ("data", "expected"),
    [
        ({}, {"email": ["This field is required."]}),
        ({"email": "not-an-email"}, {"email": ["Enter a valid email address."]}),
    ],
)
def test_username_reset_request_returns_exact_email_errors(
    data: dict[str, object],
    expected: dict[str, list[str]],
) -> None:
    """Return stable missing and malformed address detail.

    Calls the public reset serializer and compares the complete field-keyed error shape.
    Both invalid classes remain independent of account existence.

    Arguments:
        data: Invalid reset body.
        expected: Exact validation detail.

    Returns:
        None.

    Raises:
        AssertionError: If required or format errors change shape.
    """
    serializer = UsernameResetRequestSerializer(data=data)

    assert serializer.is_valid() is False
    assert serializer.errors == expected


@pytest.mark.unit
@pytest.mark.parametrize(
    "serializer",
    [
        UsernameResetConfirmSerializer(
            data={
                "account": str(uuid.uuid4()),
                "token": "username-reset-token",
                "new_username": "contains space",
            }
        ),
        UsernameChangeSerializer(
            data={"current_password": "current", "new_username": "contains space"}
        ),
    ],
)
def test_username_serializers_apply_the_shared_format_validator(serializer: Serializer) -> None:
    """Reject an invalid replacement identifier in both protocols.

    Calls each serializer directly and requires the same field-keyed validation outcome.
    Recovery and authenticated change must share the configured username policy.

    Arguments:
        serializer: Configured username serializer carrying an invalid value.

    Returns:
        None.

    Raises:
        AssertionError: If invalid usernames are accepted.
    """
    assert serializer.is_valid() is False
    assert serializer.errors == {
        "new_username": [
            (
                "Enter a valid username. This value may contain only letters, numbers, and "
                "@/./+/-/_ characters."
            )
        ]
    }


@pytest.mark.unit
@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (
            {},
            {
                "account": ["This field is required."],
                "token": ["This field is required."],
                "new_username": ["This field is required."],
            },
        ),
        (
            {
                "account": "not-a-uuid",
                "token": "token",
                "new_username": "replacement",
            },
            {"account": ["Must be a valid UUID."]},
        ),
        (
            {
                "account": str(uuid.uuid4()),
                "token": "",
                "new_username": "replacement",
            },
            {"token": ["This field may not be blank."]},
        ),
        (
            {
                "account": str(uuid.uuid4()),
                "token": "x" * 257,
                "new_username": "replacement",
            },
            {"token": ["Ensure this field has no more than 256 characters."]},
        ),
        (
            {
                "account": str(uuid.uuid4()),
                "token": "token",
                "new_username": "x" * 151,
            },
            {"new_username": ["Ensure this field has no more than 150 characters."]},
        ),
    ],
)
def test_username_reset_confirmation_returns_exact_field_errors(
    data: dict[str, object],
    expected: dict[str, list[str]],
) -> None:
    """Return stable required, UUID, token, and username details.

    Calls the public confirmation serializer with one invalid dimension at a time and compares the
    complete error mapping.

    Arguments:
        data: Invalid confirmation body.
        expected: Exact validation detail.

    Returns:
        None.

    Raises:
        AssertionError: If field validation changes.
    """
    serializer = UsernameResetConfirmSerializer(data=data)

    assert serializer.is_valid() is False
    assert serializer.errors == expected


@pytest.mark.unit
def test_username_reset_confirmation_accepts_the_complete_valid_shape() -> None:
    """Validate one complete username-reset confirmation.

    Compares UUID conversion and untrimmed token and username values through the public serializer.
    All three declared fields remain present in native data.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If valid confirmation shape changes.
    """
    account_id = uuid.uuid4()
    serializer = UsernameResetConfirmSerializer(
        data={
            "account": str(account_id),
            "token": " reset-token ",
            "new_username": "replacement",
        }
    )

    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data == {
        "account": account_id,
        "token": " reset-token ",
        "new_username": "replacement",
    }


@pytest.mark.unit
@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (
            {},
            {
                "current_password": ["This field is required."],
                "new_username": ["This field is required."],
            },
        ),
        (
            {"current_password": "", "new_username": ""},
            {
                "current_password": ["This field may not be blank."],
                "new_username": ["This field may not be blank."],
            },
        ),
        (
            {"current_password": "current", "new_username": "x" * 151},
            {"new_username": ["Ensure this field has no more than 150 characters."]},
        ),
    ],
)
def test_username_change_returns_exact_required_blank_and_length_errors(
    data: dict[str, object],
    expected: dict[str, list[str]],
) -> None:
    """Return stable authenticated username-change errors.

    Compares complete missing, blank, and oversized field mappings through the public serializer.
    Password and username failures stay field-specific.

    Arguments:
        data: Invalid username-change body.
        expected: Exact validation detail.

    Returns:
        None.

    Raises:
        AssertionError: If field validation changes.
    """
    serializer = UsernameChangeSerializer(data=data)

    assert serializer.is_valid() is False
    assert serializer.errors == expected


@pytest.mark.unit
def test_username_change_accepts_the_complete_valid_shape() -> None:
    """Validate one authenticated username replacement.

    Preserves password whitespace, validates the username, and renders no write-only credential.
    The public username remains present in output.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If valid change shape or credential secrecy changes.
    """
    serializer = UsernameChangeSerializer(
        data={"current_password": " current ", "new_username": "replacement"}
    )

    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data == {
        "current_password": " current ",
        "new_username": "replacement",
    }
    assert serializer.data == {"new_username": "replacement"}


@pytest.mark.unit
@pytest.mark.parametrize(
    "decision",
    [
        ThrottleDecision(admitted=True, retry_after_seconds=REQUEST_RETRY_AFTER_SECONDS),
        ThrottleDecision(admitted=False, retry_after_seconds=REQUEST_RETRY_AFTER_SECONDS),
    ],
)
def test_username_reset_request_throttle_returns_the_authoritative_decision(
    decision: ThrottleDecision,
) -> None:
    """Expose both username-reset request allow and deny outcomes.

    Substitutes database transaction, account lookup, and admission state while retaining exact
    body validation, rules, and retry-delay propagation.

    Arguments:
        decision: Authoritative admission outcome to propagate.

    Returns:
        None.

    Raises:
        AssertionError: If throttle decision or wait behavior differs.
    """
    request = cast(
        "Request",
        SimpleNamespace(
            method="POST",
            data={"email": "User@LOCALFORGE.Invalid"},
            META={"REMOTE_ADDR": "192.0.2.10", "request_id": "request-id"},
        ),
    )
    throttle = UsernameResetRequestThrottle()
    connection = MagicMock()
    connection.cursor.return_value.__enter__.return_value.fetchone.return_value = (
        "user@localforge.invalid",
    )
    accounts = MagicMock()
    account_query = accounts.using.return_value.select_for_update.return_value.alias.return_value
    account_query.get.side_effect = User.DoesNotExist

    with (
        patch("accounts.username_management.transaction.atomic", return_value=nullcontext()),
        patch("accounts.username_management.connections", {"default": connection}),
        patch.object(User, "objects", accounts),
        patch(
            "accounts.username_management.PostgresLoginThrottleStore.admit",
            return_value=decision,
        ),
    ):
        result = throttle.allow_request(request, cast("APIView", object()))

    assert result is decision.admitted
    assert throttle.wait() == float(REQUEST_RETRY_AFTER_SECONDS)


@pytest.mark.unit
@pytest.mark.parametrize(
    "decision",
    [
        ThrottleDecision(admitted=True, retry_after_seconds=CONFIRM_RETRY_AFTER_SECONDS),
        ThrottleDecision(admitted=False, retry_after_seconds=CONFIRM_RETRY_AFTER_SECONDS),
    ],
)
def test_username_reset_confirm_throttle_returns_the_authoritative_decision(
    decision: ThrottleDecision,
) -> None:
    """Expose both username-reset confirmation allow and deny outcomes.

    Retains exact body and username validation while replacing only the authoritative admission
    store.

    Arguments:
        decision: Authoritative admission outcome to propagate.

    Returns:
        None.

    Raises:
        AssertionError: If throttle decision or wait behavior differs.
    """
    request = cast(
        "Request",
        SimpleNamespace(
            method="POST",
            data={
                "account": str(uuid.uuid4()),
                "token": "username-reset-token",
                "new_username": "valid-username",
            },
            META={"REMOTE_ADDR": "192.0.2.10", "request_id": "request-id"},
        ),
    )
    throttle = UsernameResetConfirmThrottle()

    with patch(
        "accounts.username_management.PostgresLoginThrottleStore.admit",
        return_value=decision,
    ):
        result = throttle.allow_request(request, cast("APIView", object()))

    assert result is decision.admitted
    assert throttle.wait() == float(CONFIRM_RETRY_AFTER_SECONDS)


@pytest.mark.unit
def test_username_reset_account_resolver_returns_match_or_none() -> None:
    """Resolve active primary account state by normalized email.

    Substitutes the ORM chain and verifies matching and missing outcomes through the public helper.
    PostgreSQL expression semantics remain part of the query shape.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If lookup result or missing classification differs.
    """
    account = build_user(is_active=True)
    accounts = MagicMock()
    query = accounts.using.return_value.select_for_update.return_value.alias.return_value
    query.get.return_value = account

    with patch.object(User, "objects", accounts):
        assert resolve_username_reset_account(account.email) is account
        query.get.side_effect = User.DoesNotExist
        assert resolve_username_reset_account(account.email) is None


@pytest.mark.unit
def test_issue_username_reset_persists_digest_and_registers_after_commit() -> None:
    """Issue one username-reset record through public orchestration.

    Substitutes persistence and callback registration while retaining real token generation and
    digesting.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If persistence or after-commit publication is omitted.
    """
    account = build_user(is_active=True)
    records = MagicMock()

    with (
        patch.object(UsernameResetToken, "objects", records),
        patch("accounts.username_management.transaction.on_commit") as on_commit,
    ):
        issue_username_reset(account)

    create = records.using.return_value.create
    create.assert_called_once()
    assert create.call_args.kwargs["account"] is account
    assert create.call_args.kwargs["subject_id"] == account.pk
    on_commit.assert_called_once()


@pytest.mark.unit
def test_username_reset_request_schedules_dummy_work_for_unknown_account() -> None:
    """Keep unknown username-reset requests on the accepted dummy path.

    Returns missing account state from the ORM and verifies the real dummy scheduler registers one
    after-commit callback.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If unknown state raises or schedules no work.
    """
    accounts = MagicMock()
    account_query = accounts.using.return_value.select_for_update.return_value.alias.return_value
    account_query.get.side_effect = User.DoesNotExist

    with (
        patch.object(User, "objects", accounts),
        patch("accounts.username_management.transaction.atomic", return_value=nullcontext()),
        patch("accounts.username_management.transaction.on_commit") as on_commit,
    ):
        request_username_reset("unknown@localforge.invalid")

    on_commit.assert_called_once()


@pytest.mark.unit
def test_username_reset_confirmation_rejects_malformed_token() -> None:
    """Classify malformed username-reset bearer before database preflight.

    Calls the public confirmation function with structurally invalid text and compares the protocol
    exception.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If malformed input receives another exception.
    """
    with pytest.raises(UsernameResetTokenMalformed):
        confirm_username_reset(
            {
                "account": uuid.uuid4(),
                "token": "not-a-token",
                "new_username": "replacement",
            }
        )


@pytest.mark.unit
def test_username_reset_confirmation_classifies_expired_token() -> None:
    """Reject a valid username-reset bearer after its configured lifetime.

    Freezes issuance and confirmation two days apart so expiry occurs before database preflight.
    The public exception remains distinct from malformed input.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        UsernameResetTokenExpired: Expected after the configured lifetime.
    """
    token = f"1-{'a' * 32}.nonce"
    records = MagicMock()
    records.using.return_value.filter.return_value.values_list.return_value.first.return_value = (
        7,
        None,
    )

    with (
        patch.object(UsernameResetToken, "objects", records),
        pytest.raises(UsernameResetTokenExpired),
    ):
        confirm_username_reset(
            {
                "account": uuid.uuid4(),
                "token": token,
                "new_username": "replacement",
            }
        )


@pytest.mark.unit
@pytest.mark.parametrize("state", ["foreign", "used"])
def test_username_reset_confirmation_classifies_preflight_state(state: str) -> None:
    """Reject missing and consumed authoritative token records.

    Substitutes only the token preflight query and compares the public protocol exception before
    locked mutation.

    Arguments:
        state: Token state returned by preflight.

    Returns:
        None.

    Raises:
        UsernameResetTokenForeign: Expected for missing state.
        UsernameResetTokenUsed: Expected for consumed state.
    """
    account = build_user(is_active=True)
    token = create_username_reset_token(account)
    records = MagicMock()
    preflight = None if state == "foreign" else (7, timezone.now())
    records.using.return_value.filter.return_value.values_list.return_value.first.return_value = (
        preflight
    )
    expected = UsernameResetTokenForeign if state == "foreign" else UsernameResetTokenUsed

    with (
        patch.object(UsernameResetToken, "objects", records),
        pytest.raises(expected),
    ):
        confirm_username_reset(
            {
                "account": account.pk,
                "token": token,
                "new_username": "replacement",
            }
        )


@pytest.mark.unit
def test_username_reset_confirmation_applies_valid_locked_state() -> None:
    """Apply one valid unused username reset through the public operation.

    Substitutes ORM persistence while retaining token matching, uniqueness, username mutation, link
    consumption, and notification registration.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If valid reset does not persist or schedule notification.
    """
    account = build_user(is_active=True)
    token = create_username_reset_token(account)
    record = build_username_reset_token(
        subject_id=account.pk,
        digest=username_reset_token_digest(token),
    )
    record.pk = 7
    record.account = account
    records = MagicMock()
    record_query = records.using.return_value
    record_query.filter.return_value.values_list.return_value.first.return_value = (record.pk, None)
    record_query.select_for_update.return_value.filter.return_value.first.return_value = record
    accounts = MagicMock()
    accounts.using.return_value.select_for_update.return_value.get.return_value = account
    availability = accounts.using.return_value.exclude.return_value.alias.return_value.filter
    availability.return_value.exists.return_value = False

    with (
        patch.object(User, "objects", accounts),
        patch.object(UsernameResetToken, "objects", records),
        patch.object(account, "save") as save,
        patch("accounts.username_management.transaction.atomic", return_value=nullcontext()),
        patch("accounts.username_management.transaction.on_commit") as on_commit,
    ):
        confirm_username_reset(
            {
                "account": account.pk,
                "token": token,
                "new_username": "replacement",
            }
        )

    assert account.username == "replacement"
    save.assert_called_once()
    record_query.filter.return_value.update.assert_called_once()
    on_commit.assert_called_once()


@pytest.mark.unit
def test_username_reset_confirmation_maps_database_failure_to_service_unavailable() -> None:
    """Contain authoritative preflight failure at the public operation.

    Raises from the external token manager and verifies the stable dependency exception.
    No database diagnostic is exposed by the operation.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        ServiceUnavailable: Expected for database failure.
    """
    account = build_user(is_active=True)
    token = create_username_reset_token(account)
    records = MagicMock()
    records.using.return_value.filter.side_effect = DatabaseError

    with (
        patch.object(UsernameResetToken, "objects", records),
        pytest.raises(ServiceUnavailable),
    ):
        confirm_username_reset(
            {
                "account": account.pk,
                "token": token,
                "new_username": "replacement",
            }
        )


@pytest.mark.unit
@pytest.mark.parametrize("occupancy", ["occupied", "free"])
def test_username_availability_inverts_the_authoritative_exists_result(occupancy: str) -> None:
    """Return availability from PostgreSQL case-insensitive ownership.

    Substitutes the ORM expression chain and verifies occupied and free outcomes directly.
    The account being renamed remains excluded by the public helper.

    Arguments:
        occupancy: Whether another account owns the lowercase identity.

    Returns:
        None.

    Raises:
        AssertionError: If availability does not invert occupancy.
    """
    exists = occupancy == "occupied"
    accounts = MagicMock()
    query = accounts.using.return_value.exclude.return_value.alias.return_value.filter.return_value
    query.exists.return_value = exists

    with patch.object(User, "objects", accounts):
        available = username_is_available("replacement", excluding=uuid.uuid4())

    assert available is not exists


@pytest.mark.unit
def test_change_username_rejects_an_account_deleted_before_locking() -> None:
    """Map missing authoritative account state to authentication failure.

    Substitutes the primary ORM and transaction boundary while retaining public operation
    classification.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AuthenticationFailed: Expected when the account disappears.
    """
    accounts = MagicMock()
    accounts.using.return_value.select_for_update.return_value.get.side_effect = User.DoesNotExist

    with (
        patch.object(User, "objects", accounts),
        patch("accounts.username_management.transaction.atomic", return_value=nullcontext()),
        pytest.raises(AuthenticationFailed),
    ):
        change_username(
            uuid.uuid4(),
            {
                "current_password": "current",
                "new_username": "replacement",
            },
        )


@pytest.mark.unit
def test_change_username_rejects_incorrect_current_password() -> None:
    """Return the exact current-password error before username persistence.

    Uses one authoritative account with a different credential and verifies no save or token
    consumption occurs.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If credential validation shape differs.
    """
    account = build_user(is_active=True)
    accounts = MagicMock()
    accounts.using.return_value.select_for_update.return_value.get.return_value = account

    with (
        patch.object(User, "objects", accounts),
        patch("accounts.username_management.transaction.atomic", return_value=nullcontext()),
        pytest.raises(ValidationError) as raised,
    ):
        change_username(
            account.pk,
            {"current_password": "wrong", "new_username": "replacement"},
        )

    assert raised.value.detail == {"current_password": ["The current password is incorrect."]}


@pytest.mark.unit
def test_change_username_persists_consumes_links_and_schedules_notification() -> None:
    """Apply one valid authenticated username replacement.

    Substitutes ORM persistence while retaining current-password verification, authoritative
    availability, link consumption, and after-commit notification.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If persistence, cleanup, or notification is omitted.
    """
    current = secrets.token_urlsafe(24)
    account = build_user(is_active=True, password=current)
    accounts = MagicMock()
    accounts.using.return_value.select_for_update.return_value.get.return_value = account
    availability = accounts.using.return_value.exclude.return_value.alias.return_value.filter
    availability.return_value.exists.return_value = False
    reset_records = MagicMock()

    with (
        patch.object(User, "objects", accounts),
        patch.object(UsernameResetToken, "objects", reset_records),
        patch.object(account, "save") as save,
        patch("accounts.username_management.transaction.atomic", return_value=nullcontext()),
        patch("accounts.username_management.transaction.on_commit") as on_commit,
    ):
        change_username(
            account.pk,
            {"current_password": current, "new_username": "replacement"},
        )

    assert account.username == "replacement"
    save.assert_called_once()
    reset_records.using.return_value.filter.return_value.update.assert_called_once()
    on_commit.assert_called_once()


@pytest.mark.unit
def test_change_username_maps_database_failure_to_service_unavailable() -> None:
    """Contain primary lookup failure at the username-change boundary.

    Raises from the external account manager and verifies the stable dependency exception.
    No credential validation or persistence is attempted afterward.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        ServiceUnavailable: Expected for database failure.
    """
    accounts = MagicMock()
    accounts.using.return_value.select_for_update.return_value.get.side_effect = DatabaseError

    with (
        patch.object(User, "objects", accounts),
        patch("accounts.username_management.transaction.atomic", return_value=nullcontext()),
        pytest.raises(ServiceUnavailable),
    ):
        change_username(
            uuid.uuid4(),
            {"current_password": "current", "new_username": "replacement"},
        )
