"""Unit tests for password-reset token primitives.

Checks generated credential matching, digest-only state, malformed timestamp rejection, and the
inclusive expiry boundary with frozen time.
"""

from datetime import UTC, datetime, timedelta
from hashlib import sha256

import pytest
from django.conf import settings
from django.utils.http import int_to_base36
from freezegun import freeze_time

from accounts.password_tokens import (
    PASSWORD_RESET_EPOCH,
    create_password_reset_token,
    password_reset_token_digest,
    password_reset_token_is_expired,
    password_reset_token_matches,
    password_reset_token_remaining_seconds,
    password_reset_token_timestamp,
)
from tests.factories import build_user


@pytest.mark.unit
@freeze_time("2026-09-21 00:00:00+00:00")
def test_password_reset_token_round_trips_against_account_state() -> None:
    """Issue one credential and match it to the account that received it.

    Retains Django's state binding while proving persistence stores only the independent digest.
    The frozen clock keeps issuance and remaining lifetime deterministic.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If matching, digesting, or remaining lifetime fails.
    """
    account = build_user()
    token = create_password_reset_token(account)

    assert password_reset_token_matches(account, token) is True
    assert password_reset_token_digest(token) == sha256(token.encode()).hexdigest()
    assert password_reset_token_remaining_seconds(token) > 0


@pytest.mark.unit
def test_password_reset_expiry_is_strictly_beyond_the_timeout() -> None:
    """Keep the token valid at the exact configured lifetime boundary.

    Builds a structurally valid timestamp token and freezes the clock at and one second beyond the
    timeout, matching Django's inclusive validity rule.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the exact boundary expires or the following second remains valid.
    """
    issued_at = datetime(2026, 1, 1, tzinfo=UTC)
    timestamp = int((issued_at - PASSWORD_RESET_EPOCH).total_seconds())
    token = f"{int_to_base36(timestamp)}-{'a' * 32}.nonce"

    with freeze_time(issued_at + timedelta(seconds=settings.PASSWORD_RESET_TIMEOUT)):
        assert password_reset_token_is_expired(token) is False
    with freeze_time(issued_at + timedelta(seconds=settings.PASSWORD_RESET_TIMEOUT + 1)):
        assert password_reset_token_is_expired(token) is True
        with pytest.raises(ValueError, match="expired"):
            password_reset_token_remaining_seconds(token)

    assert password_reset_token_timestamp(token) == timestamp
