"""Password-reset token primitives.

Uses Django's password-reset token generator with digest-only persistence helpers and explicit
timestamp parsing shared by publication, delivery, and confirmation.
"""

import re
import secrets
from datetime import UTC, datetime
from hashlib import sha256
from typing import TYPE_CHECKING, cast

from django.conf import settings
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.utils import timezone
from django.utils.http import base36_to_int

if TYPE_CHECKING:
    from accounts.models import User

PASSWORD_RESET_CORE_PATTERN = re.compile(r"^[0-9a-z]+-[0-9a-f]{32}$")
PASSWORD_RESET_EPOCH = datetime(2001, 1, 1, tzinfo=UTC)
password_reset_token_generator = PasswordResetTokenGenerator()


def create_password_reset_token(account: User) -> str:
    """Create one account-state-bound password-reset bearer.

    Delegates to Django's maintained token generator, binding the value to immutable identity,
    password hash, last login, email address, timestamp, and project signing secret.

    Arguments:
        account: Account receiving the reset bearer.

    Returns:
        Django password-reset token.
    """
    core = password_reset_token_generator.make_token(account)
    return f"{core}.{secrets.token_urlsafe(24)}"


def password_reset_token_digest(token: str) -> str:
    """Digest one password-reset bearer value.

    Produces the fixed-length one-way value stored in authoritative state so a database read cannot
    recover the credential sent through email.

    Arguments:
        token: Password-reset bearer value.

    Returns:
        Hexadecimal SHA-256 digest.
    """
    return sha256(token.encode()).hexdigest()


def password_reset_token_timestamp(token: str) -> int:
    """Parse and validate one reset token timestamp segment.

    Enforces Django's complete token shape before converting the base-36 issue time, separating
    malformed input from account, expiry, and use classification.

    Arguments:
        token: Submitted password-reset token.

    Returns:
        Seconds since Django's password-reset epoch.

    Raises:
        ValueError: If the token shape or timestamp is invalid.
    """
    try:
        core, nonce = token.split(".", maxsplit=1)
    except ValueError as error:
        message = "invalid password reset token shape"
        raise ValueError(message) from error
    if PASSWORD_RESET_CORE_PATTERN.fullmatch(core) is None or not nonce:
        message = "invalid password reset token shape"
        raise ValueError(message)
    timestamp_text, _digest = core.split("-", maxsplit=1)
    return base36_to_int(timestamp_text)


def password_reset_token_age_seconds(token: str) -> int:
    """Calculate one reset token's whole-second age.

    Uses Django's token epoch and the timezone-aware project clock so frozen-time tests and runtime
    confirmation apply the same configured expiry boundary.

    Arguments:
        token: Structurally valid password-reset token.

    Returns:
        Whole seconds elapsed since issue.

    Raises:
        ValueError: If the token shape or timestamp is invalid.
    """
    timestamp = password_reset_token_timestamp(token)
    current = timezone.now()
    return int((current - PASSWORD_RESET_EPOCH).total_seconds()) - timestamp


def password_reset_token_is_expired(token: str) -> bool:
    """Decide whether one reset token exceeded its configured lifetime.

    Keeps the boundary identical to Django's generator, where a token remains valid at exactly the
    configured timeout and expires only after it.

    Arguments:
        token: Structurally valid password-reset token.

    Returns:
        Whether the token is older than the configured timeout.

    Raises:
        ValueError: If the token shape or timestamp is invalid.
    """
    timeout = cast("int", settings.PASSWORD_RESET_TIMEOUT)
    return password_reset_token_age_seconds(token) > timeout


def password_reset_token_remaining_seconds(token: str) -> int:
    """Calculate broker lifetime remaining for one reset email task.

    Rejects expired values and returns at least one second for a token still valid at Django's
    inclusive boundary, preventing queue expiry from preceding confirmation expiry.

    Arguments:
        token: Structurally valid password-reset token.

    Returns:
        Positive whole seconds remaining.

    Raises:
        ValueError: If the token is malformed or expired.
    """
    age = password_reset_token_age_seconds(token)
    timeout = cast("int", settings.PASSWORD_RESET_TIMEOUT)
    if age > timeout:
        message = "password reset token expired"
        raise ValueError(message)
    return max(1, timeout - age)


def password_reset_token_matches(account: User, token: str) -> bool:
    """Check one reset token against current authoritative account state.

    Delegates signature comparison and state binding to Django after callers have separately
    classified malformed and expired input.

    Arguments:
        account: Current primary account row.
        token: Structurally valid unexpired reset token.

    Returns:
        Whether Django accepts the token for the account's current state.
    """
    core = token.partition(".")[0]
    return password_reset_token_generator.check_token(account, core)
