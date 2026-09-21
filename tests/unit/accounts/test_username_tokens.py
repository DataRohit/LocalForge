"""Unit tests for username-reset token primitives.

Checks the dedicated signing namespace, account-state matching, digest-only persistence, malformed
input, and deterministic expiry without a database.
"""

from datetime import UTC, datetime, timedelta
from hashlib import sha256

import pytest
from django.conf import settings
from django.utils.http import int_to_base36
from freezegun import freeze_time

from accounts.username_tokens import (
    USERNAME_RESET_EPOCH,
    UsernameResetTokenGenerator,
    create_username_reset_token,
    username_reset_token_digest,
    username_reset_token_is_expired,
    username_reset_token_matches,
    username_reset_token_remaining_seconds,
    username_reset_token_timestamp,
)
from tests.factories import build_user


@pytest.mark.unit
@freeze_time("2026-09-21 00:00:00+00:00")
def test_username_reset_token_uses_its_own_account_bound_protocol() -> None:
    """Issue and validate one username-reset credential.

    Verifies the dedicated salt remains separate from password recovery and that only a digest
    needs persistence.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the salt, matching, digest, or lifetime contract changes.
    """
    account = build_user()
    token = create_username_reset_token(account)

    assert UsernameResetTokenGenerator.key_salt == "accounts.UsernameResetTokenGenerator"
    assert username_reset_token_matches(account, token) is True
    assert username_reset_token_digest(token) == sha256(token.encode()).hexdigest()
    assert username_reset_token_remaining_seconds(token) > 0


@pytest.mark.unit
def test_username_reset_expiry_is_strictly_beyond_the_timeout() -> None:
    """Keep username recovery valid at its exact configured boundary.

    Uses one structurally valid timestamp token under frozen clocks to separate the inclusive
    timeout from the first expired second.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If timestamp parsing or expiry boundary changes.
    """
    issued_at = datetime(2026, 1, 1, tzinfo=UTC)
    timestamp = int((issued_at - USERNAME_RESET_EPOCH).total_seconds())
    token = f"{int_to_base36(timestamp)}-{'a' * 32}.nonce"

    with freeze_time(issued_at + timedelta(seconds=settings.USERNAME_RESET_TIMEOUT)):
        assert username_reset_token_is_expired(token) is False
    with freeze_time(issued_at + timedelta(seconds=settings.USERNAME_RESET_TIMEOUT + 1)):
        assert username_reset_token_is_expired(token) is True
        with pytest.raises(ValueError, match="expired"):
            username_reset_token_remaining_seconds(token)

    assert username_reset_token_timestamp(token) == timestamp
