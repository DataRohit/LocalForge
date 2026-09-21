"""Unit tests for registration and profile serializers.

Exercises strict field contracts, valid-by-default account rendering, normalized email input, and
password confirmation independently from persistence and request dispatch.
"""

from contextlib import nullcontext
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast
from unittest.mock import MagicMock, patch

import pytest
from django.test import override_settings
from rest_framework.exceptions import ValidationError

from accounts.login_throttle import ThrottleDecision
from accounts.models import ActivationToken, User
from accounts.user_profiles import (
    ActivationResendResponseSerializer,
    RegistrationResponseSerializer,
    RegistrationSerializer,
    UserProfileDeletionSerializer,
    UserProfileSerializer,
    UserProfileUpdateSerializer,
    UserRegistrationThrottle,
    register_account,
)
from tests.factories import build_user

if TYPE_CHECKING:
    from rest_framework.request import Request
    from rest_framework.views import APIView

RETRY_AFTER_SECONDS = 29


@pytest.mark.unit
@override_settings(AUTH_PASSWORD_VALIDATORS=[])
def test_registration_serializer_returns_canonical_valid_data() -> None:
    """Validate one complete registration without rendering credentials.

    Uses a policy-valid password and mixed-case address, then compares normalized native data and
    write-only response behavior.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If validation, normalization, or credential secrecy changes.
    """
    serializer = RegistrationSerializer(
        data={
            "username": "profile-user",
            "email": "Profile-User@LOCALFORGE.Invalid",
            "password": "Valid-Profile-Password-123!",
            "password_confirm": "Valid-Profile-Password-123!",
        }
    )

    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["email"] == "profile-user@localforge.invalid"
    assert "password" not in serializer.data
    assert "password_confirm" not in serializer.data


@pytest.mark.unit
def test_registration_response_serializers_expose_only_public_acceptance_fields() -> None:
    """Render fixed registration and resend response shapes.

    Supplies extra source data and verifies no account state or operational value enters either
    public response.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If response fields expand or disappear.
    """
    assert RegistrationResponseSerializer(
        {
            "username": "registration-user",
            "email": "registration-user@localforge.invalid",
            "is_active": False,
        }
    ).data == {
        "username": "registration-user",
        "email": "registration-user@localforge.invalid",
    }
    assert ActivationResendResponseSerializer({"detail": "accepted", "account": "hidden"}).data == {
        "detail": "accepted"
    }


@pytest.mark.unit
def test_registration_serializer_returns_exact_confirmation_error() -> None:
    """Reject mismatched registration credentials on the confirmation field.

    Uses an otherwise valid body and compares the complete public validation shape.
    The password field itself remains free of mismatch detail.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If mismatched confirmation is accepted or reported elsewhere.
    """
    serializer = RegistrationSerializer(
        data={
            "username": "profile-user",
            "email": "profile-user@localforge.invalid",
            "password": "Valid-Profile-Password-123!",
            "password_confirm": "Different-Profile-Password-123!",
        }
    )

    assert serializer.is_valid() is False
    assert serializer.errors == {"password_confirm": ["The password confirmation does not match."]}


@pytest.mark.unit
@override_settings(
    AUTH_PASSWORD_VALIDATORS=[
        {
            "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
            "OPTIONS": {"min_length": 12},
        }
    ]
)
def test_registration_serializer_returns_exact_password_policy_error() -> None:
    """Reject a password that fails the configured public policy.

    Narrows policy to one deterministic validator and compares the complete field-keyed detail.
    Confirmation remains valid so only policy failure is reported.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If password policy is skipped or error shape changes.
    """
    serializer = RegistrationSerializer(
        data={
            "username": "profile-user",
            "email": "profile-user@localforge.invalid",
            "password": "short",
            "password_confirm": "short",
        }
    )

    assert serializer.is_valid() is False
    assert serializer.errors == {
        "password": ["This password is too short. It must contain at least 12 characters."]
    }


@pytest.mark.unit
def test_registration_serializer_reports_every_required_field() -> None:
    """Return one required error for every registration field.

    Validates an empty body and compares the complete public error mapping.
    All four fields remain mandatory.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If any registration field becomes optional.
    """
    serializer = RegistrationSerializer(data={})

    assert serializer.is_valid() is False
    assert serializer.errors == {
        "username": ["This field is required."],
        "email": ["This field is required."],
        "password": ["This field is required."],
        "password_confirm": ["This field is required."],
    }


@pytest.mark.unit
def test_registration_serializer_reports_every_blank_field() -> None:
    """Return one blank error for every registration field.

    Supplies empty text to all declared fields and compares the complete validation shape.
    No blank credential or identifier is accepted.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If blank input is accepted or reported differently.
    """
    serializer = RegistrationSerializer(
        data={"username": "", "email": "", "password": "", "password_confirm": ""}
    )

    assert serializer.is_valid() is False
    assert serializer.errors == {
        "username": ["This field may not be blank."],
        "email": ["This field may not be blank."],
        "password": ["This field may not be blank."],
        "password_confirm": ["This field may not be blank."],
    }


@pytest.mark.unit
@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("username", "x" * 151, "Ensure this field has no more than 150 characters."),
        (
            "email",
            f"{'x' * 250}@localforge.invalid",
            "Ensure this field has no more than 254 characters.",
        ),
        ("email", "not-an-email", "Enter a valid email address."),
    ],
)
def test_registration_serializer_returns_exact_field_format_errors(
    field: str,
    value: str,
    message: str,
) -> None:
    """Return stable length and email-format validation detail.

    Changes one field in an otherwise valid registration and compares the exact field error.
    Every other field remains policy-valid.

    Arguments:
        field: Registration field to invalidate.
        value: Invalid field value.
        message: Exact expected validation message.

    Returns:
        None.

    Raises:
        AssertionError: If format validation changes.
    """
    data = {
        "username": "registration-user",
        "email": "registration-user@localforge.invalid",
        "password": "Valid-Registration-Password-123!",
        "password_confirm": "Valid-Registration-Password-123!",
    }
    data[field] = value
    serializer = RegistrationSerializer(data=data)

    assert serializer.is_valid() is False
    assert serializer.errors == {field: [message]}


@pytest.mark.unit
def test_registration_serializer_rejects_undeclared_fields() -> None:
    """Return strict detail for an extra registration key.

    Adds one privileged-looking field to an otherwise valid body and requires the common strict
    serializer error.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If undeclared input is ignored.
    """
    serializer = RegistrationSerializer(
        data={
            "username": "registration-user",
            "email": "registration-user@localforge.invalid",
            "password": "Valid-Registration-Password-123!",
            "password_confirm": "Valid-Registration-Password-123!",
            "is_staff": True,
        }
    )

    assert serializer.is_valid() is False
    assert serializer.errors == {"is_staff": ["This field is not allowed."]}


@pytest.mark.unit
def test_profile_serializers_expose_and_accept_only_declared_fields() -> None:
    """Render the public profile and accept only its mutable address.

    Uses the shared valid-object factory and proves an identifier update is rejected rather than
    silently ignored.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If public or mutable profile fields drift.
    """
    account = build_user(username="profile-user", email="profile-user@localforge.invalid")

    assert UserProfileSerializer(account).data == {
        "id": str(account.pk),
        "username": "profile-user",
        "email": "profile-user@localforge.invalid",
    }

    update = UserProfileUpdateSerializer(
        data={"email": "Changed@LOCALFORGE.Invalid", "username": "forbidden"}
    )
    with pytest.raises(ValidationError) as failure:
        update.is_valid(raise_exception=True)

    assert failure.value.detail == {"username": ["This field is not allowed."]}


@pytest.mark.unit
def test_profile_deletion_accepts_only_a_write_only_current_password() -> None:
    """Validate the deletion credential without rendering it.

    Compares valid native data, empty rendering, required detail, and strict rejection of an
    account selector.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If deletion input expands or exposes the password.
    """
    valid = UserProfileDeletionSerializer(data={"current_password": "current-password"})
    assert valid.is_valid(), valid.errors
    assert valid.validated_data == {"current_password": "current-password"}
    assert valid.data == {}

    missing = UserProfileDeletionSerializer(data={})
    assert missing.is_valid() is False
    assert missing.errors == {"current_password": ["This field is required."]}

    extra = UserProfileDeletionSerializer(
        data={"current_password": "current-password", "account": "forbidden"}
    )
    assert extra.is_valid() is False
    assert extra.errors == {"account": ["This field is not allowed."]}


@pytest.mark.unit
@pytest.mark.parametrize(
    "decision",
    [
        ThrottleDecision(admitted=True, retry_after_seconds=RETRY_AFTER_SECONDS),
        ThrottleDecision(admitted=False, retry_after_seconds=RETRY_AFTER_SECONDS),
    ],
)
def test_registration_throttle_returns_the_authoritative_decision(
    decision: ThrottleDecision,
) -> None:
    """Expose both registration allow and deny outcomes.

    Replaces only primary admission storage while retaining client identity, configured rate,
    member construction, and retry-delay propagation.

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
            data={"username": "new-user"},
            META={"REMOTE_ADDR": "192.0.2.10", "request_id": "request-id"},
        ),
    )
    throttle = UserRegistrationThrottle()

    with patch(
        "accounts.user_profiles.PostgresLoginThrottleStore.admit",
        return_value=decision,
    ):
        result = throttle.allow_request(request, cast("APIView", object()))

    assert result is decision.admitted
    assert throttle.wait() == float(RETRY_AFTER_SECONDS)


@pytest.mark.unit
def test_register_account_persists_inactive_state_and_activation_after_commit() -> None:
    """Create one account through the public registration orchestration.

    Substitutes model persistence, token persistence, and transaction callbacks while retaining
    password hashing, token generation, and accepted control flow.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If account creation or activation scheduling is omitted.
    """
    records = MagicMock()

    with (
        patch.object(User, "save") as save,
        patch.object(ActivationToken, "objects", records),
        patch("accounts.user_profiles.transaction.atomic", return_value=nullcontext()),
        patch("accounts.account_activation.transaction.on_commit") as on_commit,
    ):
        register_account(
            {
                "username": "registration-user",
                "email": "registration-user@localforge.invalid",
                "password": "Valid-Registration-Password-123!",
            }
        )

    save.assert_called_once_with(using="default", force_insert=True)
    records.using.return_value.create.assert_called_once()
    on_commit.assert_called_once()
