"""Unit tests for WebSocket authentication classification.

Exercises safe exception state and pre-database subprotocol validation without resolving an
account or opening a connection.
"""

from types import SimpleNamespace
from typing import TYPE_CHECKING, cast
from unittest.mock import patch

import pytest
from django.conf import settings
from freezegun import freeze_time
from rest_framework_simplejwt.tokens import AccessToken
from rest_framework_simplejwt.utils import get_md5_hash_password

from accounts.models import User
from notifications.authentication import (
    JWTSubprotocolAuthMiddleware,
    WebSocketAuthenticationError,
    WebSocketJWTAuthentication,
)
from notifications.protocol import WebSocketOutcome
from tests.factories import build_user

if TYPE_CHECKING:
    from rest_framework_simplejwt.tokens import Token


@pytest.mark.unit
def test_authentication_error_carries_only_the_public_outcome() -> None:
    """Retain classification without credential or decoder text.

    Constructs one rejection and verifies its representation remains empty while the central
    outcome is available to the handshake boundary.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If unsafe exception text or the wrong outcome is retained.
    """
    error = WebSocketAuthenticationError(WebSocketOutcome.CREDENTIAL_MALFORMED)

    assert error.outcome is WebSocketOutcome.CREDENTIAL_MALFORMED
    assert str(error) == ""


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("subprotocols", "outcome"),
    [
        (None, WebSocketOutcome.CREDENTIAL_ABSENT),
        ([], WebSocketOutcome.CREDENTIAL_ABSENT),
        (["first", "second"], WebSocketOutcome.CREDENTIAL_MALFORMED),
        ([""], WebSocketOutcome.CREDENTIAL_MALFORMED),
    ],
)
async def test_subprotocol_validation_rejects_absent_or_ambiguous_credentials(
    subprotocols: object,
    outcome: WebSocketOutcome,
) -> None:
    """Reject credential shapes before token decoding or database work.

    Calls the middleware's public scope-resolution seam and compares the exact documented outcome.
    Rejection must occur before token decoding or account lookup.

    Arguments:
        subprotocols: Scope value to classify.
        outcome: Expected public rejection category.

    Returns:
        None.

    Raises:
        AssertionError: If the credential shape receives another outcome.
    """
    middleware = JWTSubprotocolAuthMiddleware(lambda _scope, _receive, _send: None)
    scope = {} if subprotocols is None else {"subprotocols": subprotocols}

    with pytest.raises(WebSocketAuthenticationError) as failure:
        await middleware.resolve_scope(scope)

    assert failure.value.outcome is outcome


@pytest.mark.unit
def test_websocket_authentication_resolves_one_active_current_account() -> None:
    """Return the authoritative active owner of one valid token.

    Replaces only the ORM lookup and retains identity parsing, activity, and password-revocation
    checks through the public authentication adapter.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If valid current account state is rejected.
    """
    account = build_user(is_active=True)
    token = cast(
        "Token",
        SimpleNamespace(
            payload={
                "user_id": str(account.pk),
                "hash_password": get_md5_hash_password(account.password),
            }
        ),
    )
    queryset = SimpleNamespace(get=lambda **_kwargs: account)

    with patch.object(User.objects, "using", return_value=queryset):
        resolved = WebSocketJWTAuthentication().get_user(token)

    assert resolved is account


@pytest.mark.unit
@pytest.mark.parametrize(
    ("identity", "account_state", "hash_state", "outcome"),
    [
        (7, "active", "current", WebSocketOutcome.CREDENTIAL_MALFORMED),
        ("not-a-uuid", "active", "current", WebSocketOutcome.CREDENTIAL_MALFORMED),
        (
            "018f4f10-7b6a-7c80-8a5f-111111111111",
            "missing",
            "current",
            WebSocketOutcome.ACCOUNT_NOT_FOUND,
        ),
        (
            "018f4f10-7b6a-7c80-8a5f-222222222222",
            "inactive",
            "current",
            WebSocketOutcome.ACCOUNT_INACTIVE,
        ),
        (
            "018f4f10-7b6a-7c80-8a5f-333333333333",
            "active",
            "stale",
            WebSocketOutcome.CREDENTIAL_MALFORMED,
        ),
    ],
)
def test_websocket_authentication_classifies_authoritative_account_failures(
    identity: object,
    account_state: str,
    hash_state: str,
    outcome: WebSocketOutcome,
) -> None:
    """Map malformed, missing, inactive, and revoked state to documented outcomes.

    Substitutes only the primary account lookup while retaining all public adapter classification
    branches.

    Arguments:
        identity: Token account identity claim.
        account_state: Primary account result to simulate.
        hash_state: Whether the token carries the current password hash.
        outcome: Expected WebSocket rejection category.

    Returns:
        None.

    Raises:
        AssertionError: If any state receives another outcome.
    """
    account = build_user(is_active=account_state != "inactive")
    token_hash = (
        get_md5_hash_password(account.password) if hash_state == "current" else "stale-hash"
    )
    token = cast(
        "Token",
        SimpleNamespace(payload={"user_id": identity, "hash_password": token_hash}),
    )

    def get_account(**_kwargs: object) -> User:
        """Return or reject the configured account lookup.

        Models only the external ORM result consumed by the authentication adapter.
        No database connection is created.

        Arguments:
            **_kwargs: Lookup values ignored by the fake.

        Returns:
            Configured account.

        Raises:
            User.DoesNotExist: When the test selects missing state.
        """
        if account_state == "missing":
            raise User.DoesNotExist
        return account

    queryset = SimpleNamespace(get=get_account)
    with (
        patch.object(User.objects, "using", return_value=queryset),
        pytest.raises(WebSocketAuthenticationError) as failure,
    ):
        WebSocketJWTAuthentication().get_user(token)

    assert failure.value.outcome is outcome


@pytest.mark.unit
def test_websocket_authentication_distinguishes_expired_from_malformed_tokens() -> None:
    """Classify access-token expiry separately from unusable credential text.

    Issues one access token under frozen time, advances beyond its lifetime, and compares both
    documented public outcomes.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If expiry or malformed classification changes.
    """
    with freeze_time("2026-09-21 00:00:00+00:00"):
        expired = str(AccessToken())
    authentication = WebSocketJWTAuthentication()

    with (
        freeze_time(
            f"2026-09-21 00:{settings.JWT_ACCESS_TOKEN_LIFETIME_SECONDS // 60 + 1:02d}:00+00:00"
        ),
        pytest.raises(WebSocketAuthenticationError) as expired_failure,
    ):
        authentication.get_validated_token(expired.encode())
    with pytest.raises(WebSocketAuthenticationError) as malformed_failure:
        authentication.get_validated_token(b"not-a-token")

    assert expired_failure.value.outcome is WebSocketOutcome.CREDENTIAL_EXPIRED
    assert malformed_failure.value.outcome is WebSocketOutcome.CREDENTIAL_MALFORMED
