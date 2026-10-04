"""Unit tests for JSON web token endpoint serializers.

Verifies request strictness, write-only credentials, and success response fields independently
from signing, blacklist persistence, and endpoint dispatch.
"""

from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast
from unittest.mock import MagicMock, patch

import pytest
from django.db import DatabaseError
from rest_framework.exceptions import AuthenticationFailed, ValidationError
from rest_framework_simplejwt.settings import api_settings
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
from rest_framework_simplejwt.tokens import AccessToken
from rest_framework_simplejwt.utils import get_md5_hash_password

from accounts.jwt_authentication import (
    JWTCreateResponseSerializer,
    JWTRefreshRequestSerializer,
    JWTRefreshResponseSerializer,
    JWTVerifyRequestSerializer,
    PrimaryRefreshToken,
    rotate_refresh_token,
    verify_token,
)
from accounts.models import User
from config.api_errors import ServiceUnavailable
from tests.factories import build_user

if TYPE_CHECKING:
    from collections.abc import Callable


@pytest.mark.unit
@pytest.mark.parametrize(
    ("serializer_type", "field"),
    [
        (JWTRefreshRequestSerializer, "refresh"),
        (JWTVerifyRequestSerializer, "token"),
    ],
)
def test_jwt_request_serializers_accept_one_write_only_credential(
    serializer_type: type,
    field: str,
) -> None:
    """Validate one credential and reject every undeclared field.

    Uses the public serializer seam to prove exact shape and rendering secrecy.
    Extra input must receive the shared strict-field error.

    Arguments:
        serializer_type: Request serializer class to exercise.
        field: Sole credential field declared by the serializer.

    Returns:
        None.

    Raises:
        AssertionError: If validation or write-only rendering changes.
    """
    serializer = serializer_type(data={field: "credential"})
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data == {field: "credential"}
    assert serializer.data == {}

    invalid = serializer_type(data={field: "credential", "extra": "value"})
    with pytest.raises(ValidationError) as failure:
        invalid.is_valid(raise_exception=True)

    assert failure.value.detail == {"extra": ["This field is not allowed."]}


@pytest.mark.unit
@pytest.mark.parametrize(
    ("serializer_type", "field"),
    [
        (JWTRefreshRequestSerializer, "refresh"),
        (JWTVerifyRequestSerializer, "token"),
    ],
)
@pytest.mark.parametrize(
    ("data", "expected_message"),
    [
        ({}, "This field is required."),
        ({"field": ""}, "This field may not be blank."),
    ],
)
def test_jwt_request_serializers_return_exact_missing_and_blank_errors(
    serializer_type: type,
    field: str,
    data: dict[str, str],
    expected_message: str,
) -> None:
    """Return stable required and blank credential details.

    Rewrites the generic parameter key to each public field and compares the complete serializer
    error mapping.

    Arguments:
        serializer_type: Request serializer class to exercise.
        field: Sole credential field.
        data: Generic invalid input mapping.
        expected_message: Exact field error text.

    Returns:
        None.

    Raises:
        AssertionError: If required or blank validation changes.
    """
    payload = {} if not data else {field: data["field"]}
    serializer = serializer_type(data=payload)

    assert serializer.is_valid() is False
    assert serializer.errors == {field: [expected_message]}


@pytest.mark.unit
def test_jwt_response_serializers_expose_only_protocol_credentials() -> None:
    """Render the exact create and rotation response fields.

    Supplies extra source data and verifies no account attribute enters either public response.
    Both credential exchanges must retain the same two-field shape.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If response fields expand or disappear.
    """
    data = {"access": "access-value", "refresh": "refresh-value", "username": "hidden"}

    assert JWTCreateResponseSerializer(data).data == {
        "access": "access-value",
        "refresh": "refresh-value",
    }
    assert JWTRefreshResponseSerializer(data).data == {
        "access": "access-value",
        "refresh": "refresh-value",
    }


@pytest.mark.unit
@pytest.mark.parametrize("operation", [rotate_refresh_token, verify_token])
def test_jwt_public_operations_normalize_malformed_credentials(
    operation: Callable[[str], object],
) -> None:
    """Reject malformed credentials through the stable authentication exception.

    Calls both public token helpers directly with unusable text before authoritative database state
    is needed.

    Arguments:
        operation: Public JSON web token helper to exercise.

    Returns:
        None.

    Raises:
        AuthenticationFailed: Expected for malformed credentials.
    """
    with (
        patch("accounts.jwt_authentication.transaction.atomic", return_value=nullcontext()),
        pytest.raises(AuthenticationFailed),
    ):
        operation("not-a-token")


@pytest.mark.unit
def test_refresh_rotation_returns_replacement_credentials() -> None:
    """Rotate one valid refresh through authoritative external stores.

    Uses a real signed refresh and substitutes account, outstanding, and blacklist persistence while
    retaining lock order, revocation checks, access creation, and replacement issuance.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If rotation omits either replacement credential or persistence step.
    """
    account = build_user(is_active=True)
    refresh = PrimaryRefreshToken()
    refresh[api_settings.USER_ID_CLAIM] = str(account.pk)
    refresh[api_settings.REVOKE_TOKEN_CLAIM] = get_md5_hash_password(account.password)
    encoded = str(refresh)
    outstanding_record = SimpleNamespace(jti=refresh[api_settings.JTI_CLAIM], user=account)
    accounts = MagicMock()
    accounts.using.return_value.select_for_update.return_value.get.return_value = account
    outstanding = MagicMock()
    outstanding.using.return_value.select_for_update.return_value.get.return_value = (
        outstanding_record
    )
    blacklisted = MagicMock()
    blacklisted.using.return_value.filter.return_value.exists.return_value = False

    with (
        patch.object(User, "objects", accounts),
        patch.object(OutstandingToken, "objects", outstanding),
        patch.object(BlacklistedToken, "objects", blacklisted),
        patch("accounts.jwt_authentication.transaction.atomic", return_value=nullcontext()),
    ):
        result = rotate_refresh_token(encoded)

    assert set(result) == {"access", "refresh"}
    assert result["access"]
    assert result["refresh"] != encoded
    blacklisted.using.return_value.create.assert_called_once_with(token=outstanding_record)
    outstanding.using.return_value.create.assert_called_once()


@pytest.mark.unit
@pytest.mark.parametrize("state", ["missing", "blacklisted"])
def test_refresh_rotation_rejects_missing_or_blacklisted_state(state: str) -> None:
    """Reject replay when authoritative outstanding state is absent or revoked.

    Uses one real signed refresh and varies only the external primary-store outcome.
    Both states normalize to the same public authentication failure.

    Arguments:
        state: Authoritative state to simulate.

    Returns:
        None.

    Raises:
        AuthenticationFailed: Expected for missing or blacklisted state.
    """
    account = build_user(is_active=True)
    refresh = PrimaryRefreshToken()
    refresh[api_settings.USER_ID_CLAIM] = str(account.pk)
    refresh[api_settings.REVOKE_TOKEN_CLAIM] = get_md5_hash_password(account.password)
    accounts = MagicMock()
    accounts.using.return_value.select_for_update.return_value.get.return_value = account
    outstanding = MagicMock()
    outstanding_query = outstanding.using.return_value.select_for_update.return_value
    if state == "missing":
        outstanding_query.get.side_effect = OutstandingToken.DoesNotExist
    else:
        outstanding_query.get.return_value = SimpleNamespace(user=account)
    blacklisted = MagicMock()
    blacklisted.using.return_value.filter.return_value.exists.return_value = state == "blacklisted"

    with (
        patch.object(User, "objects", accounts),
        patch.object(OutstandingToken, "objects", outstanding),
        patch.object(BlacklistedToken, "objects", blacklisted),
        patch("accounts.jwt_authentication.transaction.atomic", return_value=nullcontext()),
        pytest.raises(AuthenticationFailed),
    ):
        rotate_refresh_token(str(refresh))


@pytest.mark.unit
def test_refresh_rotation_maps_database_failure_to_service_unavailable() -> None:
    """Contain primary persistence failure at the rotation boundary.

    Raises from outstanding-token lookup after valid account resolution and verifies the stable
    dependency exception.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        ServiceUnavailable: Expected for database failure.
    """
    account = build_user(is_active=True)
    refresh = PrimaryRefreshToken()
    refresh[api_settings.USER_ID_CLAIM] = str(account.pk)
    refresh[api_settings.REVOKE_TOKEN_CLAIM] = get_md5_hash_password(account.password)
    accounts = MagicMock()
    accounts.using.return_value.select_for_update.return_value.get.return_value = account
    outstanding = MagicMock()
    outstanding.using.return_value.select_for_update.return_value.get.side_effect = DatabaseError
    blacklisted = MagicMock()
    blacklisted.using.return_value.filter.return_value.exists.return_value = False

    with (
        patch.object(User, "objects", accounts),
        patch.object(OutstandingToken, "objects", outstanding),
        patch.object(BlacklistedToken, "objects", blacklisted),
        patch("accounts.jwt_authentication.transaction.atomic", return_value=nullcontext()),
        pytest.raises(ServiceUnavailable),
    ):
        rotate_refresh_token(str(refresh))


@pytest.mark.unit
def test_token_verification_accepts_current_unrevoked_access() -> None:
    """Verify one current access token without returning credential contents.

    Uses a real signed access and substitutes blacklist and account lookup while retaining token
    type, revocation, and password-state checks.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If valid verification raises or skips primary lookup.
    """
    account = build_user(is_active=True)
    access = AccessToken()
    access[api_settings.USER_ID_CLAIM] = str(account.pk)
    access[api_settings.REVOKE_TOKEN_CLAIM] = get_md5_hash_password(account.password)
    accounts = MagicMock()
    accounts.using.return_value.get.return_value = account
    blacklisted = MagicMock()
    blacklisted.using.return_value.filter.return_value.exists.return_value = False

    with (
        patch.object(User, "objects", accounts),
        patch.object(BlacklistedToken, "objects", blacklisted),
    ):
        verify_token(str(access))


@pytest.mark.unit
@pytest.mark.parametrize("failure", ["blacklisted", "database"])
def test_token_verification_rejects_revoked_or_unavailable_state(failure: str) -> None:
    """Reject blacklist replay and map primary loss to dependency failure.

    Uses one real signed access and varies only the authoritative external result.
    Public exceptions remain distinct for credential rejection and dependency loss.

    Arguments:
        failure: External failure mode to simulate.

    Returns:
        None.

    Raises:
        AuthenticationFailed: Expected for blacklist replay.
        ServiceUnavailable: Expected for primary database failure.
    """
    account = build_user(is_active=True)
    access = AccessToken()
    access[api_settings.USER_ID_CLAIM] = str(account.pk)
    access[api_settings.REVOKE_TOKEN_CLAIM] = get_md5_hash_password(account.password)
    accounts = MagicMock()
    blacklisted = MagicMock()
    blacklisted.using.return_value.filter.return_value.exists.return_value = (
        failure == "blacklisted"
    )
    if failure == "database":
        accounts.using.return_value.get.side_effect = DatabaseError
    else:
        accounts.using.return_value.get.return_value = account
    expected = ServiceUnavailable if failure == "database" else AuthenticationFailed

    with (
        patch.object(User, "objects", accounts),
        patch.object(BlacklistedToken, "objects", blacklisted),
        pytest.raises(expected),
    ):
        verify_token(str(access))


@pytest.mark.unit
@pytest.mark.parametrize("operation", [rotate_refresh_token, verify_token])
def test_jwt_public_operations_reject_invalid_input_types(
    operation: Callable[[str], object],
) -> None:
    """Normalize non-text credential input to authentication failure.

    Passes an object through the public typed boundary at runtime and verifies no package type
    detail escapes.

    Arguments:
        operation: Public JSON web token helper to exercise.

    Returns:
        None.

    Raises:
        AuthenticationFailed: Expected for invalid runtime type.
    """
    with (
        patch("accounts.jwt_authentication.transaction.atomic", return_value=nullcontext()),
        pytest.raises(AuthenticationFailed),
    ):
        operation(cast("str", object()))
