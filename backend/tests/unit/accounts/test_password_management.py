"""Unit tests for password-management serializers.

Exercises normalized recovery identity, strict request shape, matching replacement confirmation,
and write-only password rendering without database state.
"""

from __future__ import annotations

import secrets
import uuid
from contextlib import nullcontext
from datetime import timedelta
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast
from unittest.mock import MagicMock, patch

import pytest
from django.db import DatabaseError
from django.test import override_settings
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.exceptions import AuthenticationFailed, ValidationError
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken

from accounts.login_throttle import ThrottleDecision
from accounts.models import PasswordResetToken, User
from accounts.password_management import (
    PasswordChangeSerializer,
    PasswordResetConfirmSerializer,
    PasswordResetConfirmThrottle,
    PasswordResetRequestSerializer,
    PasswordResetRequestThrottle,
    PasswordResetResponseSerializer,
    change_password,
    confirm_password_reset,
    issue_password_reset,
    request_password_reset,
    resolve_password_reset_account,
    revoke_account_credentials,
)
from accounts.password_tokens import create_password_reset_token, password_reset_token_digest
from config.api_errors import (
    PasswordResetTokenExpired,
    PasswordResetTokenForeign,
    PasswordResetTokenMalformed,
    PasswordResetTokenUsed,
    ServiceUnavailable,
)
from tests.factories import build_password_reset_token, build_user

if TYPE_CHECKING:
    from rest_framework.request import Request
    from rest_framework.serializers import Serializer
    from rest_framework.views import APIView

REQUEST_RETRY_AFTER_SECONDS = 19
CONFIRM_RETRY_AFTER_SECONDS = 23


@pytest.mark.unit
def test_password_reset_request_normalizes_and_rejects_extra_fields() -> None:
    """Accept one canonical email and reject undeclared input.

    Compares exact validated data and field-keyed strict-shape detail.
    No recovery lookup or throttle state is involved.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If normalization or strictness changes.
    """
    serializer = PasswordResetRequestSerializer(data={"email": "User@LOCALFORGE.Invalid"})

    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data == {"email": "user@localforge.invalid"}

    invalid = PasswordResetRequestSerializer(
        data={"email": "user@localforge.invalid", "username": "not-allowed"}
    )
    with pytest.raises(ValidationError) as failure:
        invalid.is_valid(raise_exception=True)

    assert failure.value.detail == {"username": ["This field is not allowed."]}


@pytest.mark.unit
def test_password_reset_response_exposes_only_the_accepted_detail() -> None:
    """Render the enumeration-resistant reset response.

    Supplies extra source state and verifies only the fixed detail field reaches output.
    No account or token value can enter the response.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If response fields expand or disappear.
    """
    assert PasswordResetResponseSerializer({"detail": "accepted", "account": "hidden"}).data == {
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
def test_password_reset_request_returns_exact_email_errors(
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
    serializer = PasswordResetRequestSerializer(data=data)

    assert serializer.is_valid() is False
    assert serializer.errors == expected


@pytest.mark.unit
def test_password_reset_confirmation_reports_every_missing_field() -> None:
    """Return one required error for every confirmation field.

    Validates an empty object through the public serializer and compares the complete error mapping.
    No field may become optional without changing the public contract.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If a field becomes optional or error shape changes.
    """
    serializer = PasswordResetConfirmSerializer(data={})

    assert serializer.is_valid() is False
    assert serializer.errors == {
        "account": ["This field is required."],
        "token": ["This field is required."],
        "new_password": ["This field is required."],
        "new_password_confirm": ["This field is required."],
    }


@pytest.mark.unit
@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (
            {
                "account": "not-a-uuid",
                "token": "token",
                "new_password": "replacement",
                "new_password_confirm": "replacement",
            },
            {"account": ["Must be a valid UUID."]},
        ),
        (
            {
                "account": str(uuid.uuid4()),
                "token": "",
                "new_password": "replacement",
                "new_password_confirm": "replacement",
            },
            {"token": ["This field may not be blank."]},
        ),
        (
            {
                "account": str(uuid.uuid4()),
                "token": "x" * 257,
                "new_password": "replacement",
                "new_password_confirm": "replacement",
            },
            {"token": ["Ensure this field has no more than 256 characters."]},
        ),
    ],
)
def test_password_reset_confirmation_returns_exact_field_errors(
    data: dict[str, object],
    expected: dict[str, list[str]],
) -> None:
    """Return stable UUID, blank-token, and oversized-token details.

    Calls the public confirmation serializer with otherwise valid fields and compares the complete
    error mapping.

    Arguments:
        data: Invalid confirmation body.
        expected: Exact validation detail.

    Returns:
        None.

    Raises:
        AssertionError: If field validation changes.
    """
    serializer = PasswordResetConfirmSerializer(data=data)

    assert serializer.is_valid() is False
    assert serializer.errors == expected


@pytest.mark.unit
def test_password_reset_confirmation_accepts_the_complete_valid_shape() -> None:
    """Validate one complete reset confirmation.

    Compares UUID conversion, untrimmed credential fields, and matching replacement values through
    the public serializer.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If valid confirmation shape changes.
    """
    account_id = uuid.uuid4()
    serializer = PasswordResetConfirmSerializer(
        data={
            "account": str(account_id),
            "token": " reset-token ",
            "new_password": " replacement ",
            "new_password_confirm": " replacement ",
        }
    )

    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data == {
        "account": account_id,
        "token": " reset-token ",
        "new_password": " replacement ",
        "new_password_confirm": " replacement ",
    }


@pytest.mark.unit
@pytest.mark.parametrize(
    "serializer",
    [
        PasswordResetConfirmSerializer(
            data={
                "account": str(uuid.uuid4()),
                "token": "reset-token",
                "new_password": "replacement",
                "new_password_confirm": "different",
            }
        ),
        PasswordChangeSerializer(
            data={
                "current_password": "current",
                "new_password": "replacement",
                "new_password_confirm": "different",
            }
        ),
    ],
)
def test_password_serializers_reject_mismatched_confirmation(serializer: Serializer) -> None:
    """Return the exact confirmation error for both replacement protocols.

    Exercises the serializer public validation seam without invoking account-aware password policy.
    Both replacement protocols must report the same confirmation field.

    Arguments:
        serializer: Configured password serializer with mismatched confirmation.

    Returns:
        None.

    Raises:
        AssertionError: If mismatch detail changes or becomes accepted.
    """
    with pytest.raises(ValidationError) as failure:
        serializer.is_valid(raise_exception=True)

    assert failure.value.detail == {
        "new_password_confirm": ["The password confirmation does not match."]
    }


@pytest.mark.unit
@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (
            {},
            {
                "current_password": ["This field is required."],
                "new_password": ["This field is required."],
                "new_password_confirm": ["This field is required."],
            },
        ),
        (
            {
                "current_password": "",
                "new_password": "",
                "new_password_confirm": "",
            },
            {
                "current_password": ["This field may not be blank."],
                "new_password": ["This field may not be blank."],
                "new_password_confirm": ["This field may not be blank."],
            },
        ),
    ],
)
def test_password_change_returns_exact_required_and_blank_errors(
    data: dict[str, object],
    expected: dict[str, list[str]],
) -> None:
    """Return stable password-change field errors.

    Compares complete missing and blank mappings for all three write-only fields.
    No credential value is rendered during validation.

    Arguments:
        data: Invalid password-change body.
        expected: Exact validation detail.

    Returns:
        None.

    Raises:
        AssertionError: If required or blank validation changes.
    """
    serializer = PasswordChangeSerializer(data=data)

    assert serializer.is_valid() is False
    assert serializer.errors == expected


@pytest.mark.unit
def test_password_change_accepts_the_complete_valid_shape() -> None:
    """Validate one complete authenticated password replacement.

    Preserves surrounding whitespace and write-only rendering while converting no field value.
    Confirmation equality remains the only serializer-level cross-field rule.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If valid change shape or secrecy changes.
    """
    data = {
        "current_password": " current ",
        "new_password": " replacement ",
        "new_password_confirm": " replacement ",
    }
    serializer = PasswordChangeSerializer(data=data)

    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data == data
    assert serializer.data == {}


@pytest.mark.unit
@pytest.mark.parametrize(
    "decision",
    [
        ThrottleDecision(admitted=True, retry_after_seconds=REQUEST_RETRY_AFTER_SECONDS),
        ThrottleDecision(admitted=False, retry_after_seconds=REQUEST_RETRY_AFTER_SECONDS),
    ],
)
def test_password_reset_request_throttle_returns_the_authoritative_decision(
    decision: ThrottleDecision,
) -> None:
    """Expose both request-throttle allow and deny outcomes.

    Substitutes database transaction, account lookup, and admission state while retaining exact
    body validation, rule construction, and retry delay propagation.

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
    throttle = PasswordResetRequestThrottle()
    connection = MagicMock()
    connection.cursor.return_value.__enter__.return_value.fetchone.return_value = (
        "user@localforge.invalid",
    )
    accounts = MagicMock()
    account_query = accounts.using.return_value.select_for_update.return_value.alias.return_value
    account_query.get.side_effect = User.DoesNotExist

    with (
        patch("accounts.password_management.transaction.atomic", return_value=nullcontext()),
        patch("accounts.password_management.connections", {"default": connection}),
        patch.object(User, "objects", accounts),
        patch(
            "accounts.password_management.PostgresLoginThrottleStore.admit",
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
@override_settings(AUTH_PASSWORD_VALIDATORS=[])
def test_password_reset_confirm_throttle_returns_the_authoritative_decision(
    decision: ThrottleDecision,
) -> None:
    """Expose both confirmation-throttle allow and deny outcomes.

    Retains exact body and confirmation validation while replacing only the authoritative admission
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
                "token": "reset-token",
                "new_password": "Valid-Reset-Password-123!",
                "new_password_confirm": "Valid-Reset-Password-123!",
            },
            META={"REMOTE_ADDR": "192.0.2.10", "request_id": "request-id"},
        ),
    )
    throttle = PasswordResetConfirmThrottle()

    with patch(
        "accounts.password_management.PostgresLoginThrottleStore.admit",
        return_value=decision,
    ):
        result = throttle.allow_request(request, cast("APIView", object()))

    assert result is decision.admitted
    assert throttle.wait() == float(CONFIRM_RETRY_AFTER_SECONDS)


@pytest.mark.unit
def test_password_reset_account_resolver_returns_match_or_none() -> None:
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
        assert resolve_password_reset_account(account.email) is account
        query.get.side_effect = User.DoesNotExist
        assert resolve_password_reset_account(account.email) is None


@pytest.mark.unit
def test_issue_password_reset_persists_digest_and_registers_after_commit() -> None:
    """Issue one password-reset record through public orchestration.

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
        patch.object(PasswordResetToken, "objects", records),
        patch("accounts.password_management.transaction.on_commit") as on_commit,
    ):
        issue_password_reset(account)

    create = records.using.return_value.create
    create.assert_called_once()
    assert create.call_args.kwargs["account"] is account
    assert create.call_args.kwargs["subject_id"] == account.pk
    on_commit.assert_called_once()


@pytest.mark.unit
def test_password_reset_request_schedules_dummy_work_for_unknown_account() -> None:
    """Keep unknown reset requests on the accepted dummy path.

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
        patch("accounts.password_management.transaction.atomic", return_value=nullcontext()),
        patch("accounts.password_management.transaction.on_commit") as on_commit,
    ):
        request_password_reset("unknown@localforge.invalid")

    on_commit.assert_called_once()


@pytest.mark.unit
def test_password_reset_confirmation_rejects_malformed_token() -> None:
    """Classify malformed reset bearer before database preflight.

    Calls the public confirmation function with structurally invalid text and compares the protocol
    exception.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If malformed input receives another exception.
    """
    with pytest.raises(PasswordResetTokenMalformed):
        confirm_password_reset(
            {
                "account": uuid.uuid4(),
                "token": "not-a-token",
                "new_password": "Valid-New-Password-123!",
            }
        )


@pytest.mark.unit
def test_password_reset_confirmation_classifies_expired_token() -> None:
    """Reject a valid reset bearer after its configured lifetime.

    Freezes issuance and confirmation two days apart so expiry occurs before database preflight.
    The public exception remains distinct from malformed input.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        PasswordResetTokenExpired: Expected after the configured lifetime.
    """
    token = f"1-{'a' * 32}.nonce"
    records = MagicMock()
    records.using.return_value.filter.return_value.values_list.return_value.first.return_value = (
        7,
        None,
    )

    with (
        patch.object(PasswordResetToken, "objects", records),
        pytest.raises(PasswordResetTokenExpired),
    ):
        confirm_password_reset(
            {
                "account": uuid.uuid4(),
                "token": token,
                "new_password": "Valid-New-Password-123!",
            }
        )


@pytest.mark.unit
@pytest.mark.parametrize("state", ["foreign", "used"])
def test_password_reset_confirmation_classifies_preflight_state(state: str) -> None:
    """Reject missing and consumed authoritative token records.

    Substitutes only the token preflight query and compares the public protocol exception before
    locked mutation.

    Arguments:
        state: Token state returned by preflight.

    Returns:
        None.

    Raises:
        PasswordResetTokenForeign: Expected for missing state.
        PasswordResetTokenUsed: Expected for consumed state.
    """
    account = build_user(is_active=True)
    token = create_password_reset_token(account)
    records = MagicMock()
    preflight = None if state == "foreign" else (7, timezone.now())
    records.using.return_value.filter.return_value.values_list.return_value.first.return_value = (
        preflight
    )
    expected = PasswordResetTokenForeign if state == "foreign" else PasswordResetTokenUsed

    with (
        patch.object(PasswordResetToken, "objects", records),
        pytest.raises(expected),
    ):
        confirm_password_reset(
            {
                "account": account.pk,
                "token": token,
                "new_password": "Valid-New-Password-123!",
            }
        )


@pytest.mark.unit
@override_settings(AUTH_PASSWORD_VALIDATORS=[])
def test_password_reset_confirmation_applies_valid_locked_state() -> None:
    """Apply one valid unused reset through the public operation.

    Substitutes ORM persistence and credential stores while retaining token matching, password
    mutation, link consumption, revocation orchestration, and notification registration.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If valid reset does not persist or schedule notification.
    """
    current_password = secrets.token_urlsafe(24)
    account = build_user(is_active=True, password=current_password)
    token = create_password_reset_token(account)
    record = build_password_reset_token(
        subject_id=account.pk,
        digest=password_reset_token_digest(token),
    )
    record.pk = 7
    record.account = account
    records = MagicMock()
    record_query = records.using.return_value
    record_query.filter.return_value.values_list.return_value.first.return_value = (record.pk, None)
    record_query.select_for_update.return_value.filter.return_value.first.return_value = record
    accounts = MagicMock()
    accounts.using.return_value.select_for_update.return_value.get.return_value = account
    secondary = MagicMock()
    outstanding = MagicMock()
    outstanding.using.return_value.select_for_update.return_value.filter.return_value = []
    blacklisted = MagicMock()

    with (
        patch.object(User, "objects", accounts),
        patch.object(PasswordResetToken, "objects", records),
        patch.object(Token, "objects", secondary),
        patch.object(OutstandingToken, "objects", outstanding),
        patch.object(BlacklistedToken, "objects", blacklisted),
        patch.object(account, "save") as save,
        patch("accounts.password_management.transaction.atomic", return_value=nullcontext()),
        patch("accounts.password_management.transaction.on_commit") as on_commit,
    ):
        confirm_password_reset(
            {
                "account": account.pk,
                "token": token,
                "new_password": "Valid-New-Password-123!",
            }
        )

    assert account.check_password("Valid-New-Password-123!")
    save.assert_called_once()
    record_query.filter.return_value.update.assert_called_once()
    secondary.using.return_value.filter.return_value.delete.assert_called_once()
    on_commit.assert_called_once()


@pytest.mark.unit
def test_password_reset_confirmation_maps_database_failure_to_service_unavailable() -> None:
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
    token = create_password_reset_token(account)
    records = MagicMock()
    records.using.return_value.filter.side_effect = DatabaseError

    with (
        patch.object(PasswordResetToken, "objects", records),
        pytest.raises(ServiceUnavailable),
    ):
        confirm_password_reset(
            {
                "account": account.pk,
                "token": token,
                "new_password": "Valid-New-Password-123!",
            }
        )


@pytest.mark.unit
def test_revoke_account_credentials_deletes_token_and_blacklists_refreshes() -> None:
    """Revoke both credential families through public orchestration.

    Substitutes token managers and verifies secondary deletion plus primary blacklist creation for
    every outstanding refresh.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If either credential family remains unreached.
    """
    account = build_user(is_active=True)
    secondary = MagicMock()
    outstanding = MagicMock()
    now = timezone.now()
    refresh_value = secrets.token_urlsafe(16)
    refresh = OutstandingToken(
        id=7,
        user=account,
        jti="refresh-jti",
        token=refresh_value,
        created_at=now,
        expires_at=now + timedelta(hours=1),
    )
    outstanding.using.return_value.select_for_update.return_value.filter.return_value = [refresh]
    blacklisted = MagicMock()

    with (
        patch.object(Token, "objects", secondary),
        patch.object(OutstandingToken, "objects", outstanding),
        patch.object(BlacklistedToken, "objects", blacklisted),
    ):
        revoke_account_credentials(account)

    secondary.using.return_value.filter.return_value.delete.assert_called_once()
    blacklisted.using.return_value.bulk_create.assert_called_once()


@pytest.mark.unit
def test_change_password_rejects_an_account_deleted_before_locking() -> None:
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
        patch("accounts.password_management.transaction.atomic", return_value=nullcontext()),
        pytest.raises(AuthenticationFailed),
    ):
        change_password(
            uuid.uuid4(),
            {
                "current_password": "current",
                "new_password": "replacement",
            },
        )


@pytest.mark.unit
@pytest.mark.parametrize("failure", ["wrong-current", "same-new"])
def test_change_password_returns_exact_credential_validation(failure: str) -> None:
    """Reject an incorrect current password or unchanged replacement.

    Uses one current account and compares the public field-specific error before any persistence or
    revocation side effect.

    Arguments:
        failure: Credential failure mode to exercise.

    Returns:
        None.

    Raises:
        AssertionError: If validation error shape differs.
    """
    current = secrets.token_urlsafe(24)
    account = build_user(is_active=True, password=current)
    accounts = MagicMock()
    accounts.using.return_value.select_for_update.return_value.get.return_value = account
    validated = {
        "current_password": "wrong" if failure == "wrong-current" else current,
        "new_password": current if failure == "same-new" else "replacement",
    }
    expected = (
        {"current_password": ["The current password is incorrect."]}
        if failure == "wrong-current"
        else {"new_password": ["The new password must differ from the current password."]}
    )

    with (
        patch.object(User, "objects", accounts),
        patch("accounts.password_management.transaction.atomic", return_value=nullcontext()),
        pytest.raises(ValidationError) as raised,
    ):
        change_password(account.pk, validated)

    assert raised.value.detail == expected


@pytest.mark.unit
@override_settings(AUTH_PASSWORD_VALIDATORS=[])
def test_change_password_persists_and_revokes_credentials() -> None:
    """Apply one valid authenticated password replacement.

    Substitutes ORM persistence and credential stores while retaining password verification,
    hashing, reset-link consumption, and both revocation families.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If persistence or revocation is omitted.
    """
    current = secrets.token_urlsafe(24)
    replacement = secrets.token_urlsafe(24)
    account = build_user(is_active=True, password=current)
    accounts = MagicMock()
    accounts.using.return_value.select_for_update.return_value.get.return_value = account
    reset_records = MagicMock()
    secondary = MagicMock()
    outstanding = MagicMock()
    outstanding.using.return_value.select_for_update.return_value.filter.return_value = []
    blacklisted = MagicMock()

    with (
        patch.object(User, "objects", accounts),
        patch.object(PasswordResetToken, "objects", reset_records),
        patch.object(Token, "objects", secondary),
        patch.object(OutstandingToken, "objects", outstanding),
        patch.object(BlacklistedToken, "objects", blacklisted),
        patch.object(account, "save") as save,
        patch("accounts.password_management.transaction.atomic", return_value=nullcontext()),
    ):
        change_password(
            account.pk,
            {"current_password": current, "new_password": replacement},
        )

    assert account.check_password(replacement)
    save.assert_called_once()
    reset_records.using.return_value.filter.return_value.update.assert_called_once()
    secondary.using.return_value.filter.return_value.delete.assert_called_once()


@pytest.mark.unit
def test_change_password_maps_database_failure_to_service_unavailable() -> None:
    """Contain primary lookup failure at the password-change boundary.

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
        patch("accounts.password_management.transaction.atomic", return_value=nullcontext()),
        pytest.raises(ServiceUnavailable),
    ):
        change_password(
            uuid.uuid4(),
            {"current_password": "current", "new_password": "replacement"},
        )
