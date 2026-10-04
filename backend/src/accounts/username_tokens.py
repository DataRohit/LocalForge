"""Username-reset token primitives.

Uses a dedicated Django token generator with digest-only persistence helpers and explicit
timestamp parsing shared by publication, delivery, and confirmation.
"""

from __future__ import annotations

import re
import secrets
from datetime import UTC, datetime
from hashlib import sha256
from typing import TYPE_CHECKING, cast, override

from django.conf import settings
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.utils import timezone
from django.utils.crypto import constant_time_compare
from django.utils.http import base36_to_int

if TYPE_CHECKING:
    from django.contrib.auth.base_user import AbstractBaseUser

    from accounts.models import User

USERNAME_RESET_CORE_PATTERN = re.compile(r"^[0-9a-z]+-[0-9a-f]{32}$")
USERNAME_RESET_EPOCH = datetime(2001, 1, 1, tzinfo=UTC)


class UsernameResetTokenGenerator(PasswordResetTokenGenerator):
    """Generate account-state-bound username-reset tokens.

    Inherits from Django's ``PasswordResetTokenGenerator`` and separates this protocol from
    password recovery with a dedicated signing salt while retaining maintained state binding.

    Attributes:
        key_salt: Dedicated signing namespace for username recovery.

    Members:
        None beyond those inherited from ``PasswordResetTokenGenerator``.
    """

    key_salt = "accounts.UsernameResetTokenGenerator"

    @override
    def check_token(self, user: AbstractBaseUser | None, token: str | None) -> bool:
        """Validate one username-reset token against state and username lifetime.

        Reuses Django's timestamp parsing, secret rotation, HMAC reconstruction, and account-state
        binding while applying this protocol's independent configured timeout.

        Arguments:
            user: Current account state to authenticate, or None.
            token: Django token core to authenticate, or None.

        Returns:
            Whether signature, account state, and username-reset age are valid.
        """
        if not user or not token:
            return False
        try:
            timestamp_text, _digest = token.split("-")
            timestamp = base36_to_int(timestamp_text)
        except ValueError:
            return False

        account = cast("User", user)
        for secret in [self.secret, *self.secret_fallbacks]:
            expected = self._make_token_with_timestamp(account, timestamp, secret)
            if constant_time_compare(expected, token):
                break
        else:
            return False

        age = self._num_seconds(self._now()) - timestamp
        timeout = cast("int", settings.USERNAME_RESET_TIMEOUT)
        return age <= timeout


username_reset_token_generator = UsernameResetTokenGenerator()


def create_username_reset_token(account: User) -> str:
    """Create one account-state-bound username-reset bearer.

    Delegates to Django's maintained generator, binding the value to immutable identity, password,
    last login, email address, timestamp, and the project signing secret.

    Arguments:
        account: Account receiving the reset bearer.

    Returns:
        Django username-reset token plus an independent issuance nonce.
    """
    core = username_reset_token_generator.make_token(account)
    return f"{core}.{secrets.token_urlsafe(24)}"


def username_reset_token_digest(token: str) -> str:
    """Digest one username-reset bearer value.

    Produces the fixed-length one-way value stored in authoritative state so a database read cannot
    recover the credential sent through email.

    Arguments:
        token: Username-reset bearer value.

    Returns:
        Hexadecimal SHA-256 digest.
    """
    return sha256(token.encode()).hexdigest()


def username_reset_token_timestamp(token: str) -> int:
    """Parse and validate one username-reset timestamp segment.

    Enforces Django's complete token shape before converting the base-36 issue time, separating
    malformed input from account, expiry, and use classification.

    Arguments:
        token: Submitted username-reset token.

    Returns:
        Seconds since Django's token epoch.

    Raises:
        ValueError: If the token shape or timestamp is invalid.
    """
    try:
        core, nonce = token.split(".", maxsplit=1)
    except ValueError as error:
        message = "invalid username reset token shape"
        raise ValueError(message) from error
    if USERNAME_RESET_CORE_PATTERN.fullmatch(core) is None or not nonce:
        message = "invalid username reset token shape"
        raise ValueError(message)
    timestamp_text, _digest = core.split("-", maxsplit=1)
    return base36_to_int(timestamp_text)


def username_reset_token_age_seconds(token: str) -> int:
    """Calculate one username-reset token's whole-second age.

    Uses Django's token epoch and the timezone-aware project clock so frozen-time tests and runtime
    confirmation apply the same configured expiry boundary.

    Arguments:
        token: Structurally valid username-reset token.

    Returns:
        Whole seconds elapsed since issue.

    Raises:
        ValueError: If the token shape or timestamp is invalid.
    """
    timestamp = username_reset_token_timestamp(token)
    current = timezone.now()
    return int((current - USERNAME_RESET_EPOCH).total_seconds()) - timestamp


def username_reset_token_is_expired(token: str) -> bool:
    """Decide whether one username-reset token exceeded its configured lifetime.

    Keeps the boundary identical to Django's generator, where a token remains valid at exactly the
    configured timeout and expires only after it.

    Arguments:
        token: Structurally valid username-reset token.

    Returns:
        Whether the token is older than the configured timeout.

    Raises:
        ValueError: If the token shape or timestamp is invalid.
    """
    timeout = cast("int", settings.USERNAME_RESET_TIMEOUT)
    return username_reset_token_age_seconds(token) > timeout


def username_reset_token_remaining_seconds(token: str) -> int:
    """Calculate broker lifetime remaining for one username-reset email task.

    Rejects expired values and returns at least one second for a token still valid at Django's
    inclusive boundary, preventing queue expiry from preceding confirmation expiry.

    Arguments:
        token: Structurally valid username-reset token.

    Returns:
        Positive whole seconds remaining.

    Raises:
        ValueError: If the token is malformed or expired.
    """
    age = username_reset_token_age_seconds(token)
    timeout = cast("int", settings.USERNAME_RESET_TIMEOUT)
    if age > timeout:
        message = "username reset token expired"
        raise ValueError(message)
    return max(1, timeout - age)


def username_reset_token_matches(account: User, token: str) -> bool:
    """Check one username-reset token against current authoritative account state.

    Delegates signature comparison and state binding to Django after callers have separately
    classified malformed and expired input.

    Arguments:
        account: Current primary account row.
        token: Structurally valid unexpired username-reset token.

    Returns:
        Whether Django accepts the token for the account's current state.
    """
    core = token.partition(".")[0]
    return username_reset_token_generator.check_token(account, core)
