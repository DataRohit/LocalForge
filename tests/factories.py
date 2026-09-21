"""Valid-by-default test object factories.

Builds unsaved account and credential-state objects for isolated tests and creates accounts through
the project manager only when an integration test explicitly requests persistence.
"""

from __future__ import annotations

import itertools
import secrets
import uuid
from typing import TYPE_CHECKING

from django.utils import timezone

from accounts.models import (
    ActivationToken,
    LoginThrottleEvent,
    PasswordResetToken,
    User,
    UsernameResetToken,
)

if TYPE_CHECKING:
    from datetime import datetime

_sequence = itertools.count()


def build_user(
    *,
    username: str | None = None,
    email: str | None = None,
    password: str | None = None,
    is_active: bool = False,
    is_staff: bool = False,
) -> User:
    """Build one valid unsaved account.

    Supplies unique identifiers and a usable password by default while allowing a test to override
    only the field relevant to its behavior.

    Arguments:
        username: Optional username override.
        email: Optional address override.
        password: Optional raw password override.
        is_active: Whether the account is active.
        is_staff: Whether the account may enter administration.

    Returns:
        Valid unsaved account object.

    Raises:
        None.
    """
    index = next(_sequence)
    account = User(
        username=f"factory-user-{index}" if username is None else username,
        email=f"factory-user-{index}@localforge.invalid" if email is None else email,
        is_active=is_active,
        is_staff=is_staff,
        is_superuser=False,
    )
    account.set_password(secrets.token_urlsafe(24) if password is None else password)

    return account


def create_user(
    *,
    username: str | None = None,
    email: str | None = None,
    password: str | None = None,
    is_active: bool = False,
) -> User:
    """Create one valid persisted account through the project manager.

    Keeps integration setup on the same public creation seam as application code while preserving
    unique valid identifiers by default.

    Arguments:
        username: Optional username override.
        email: Optional address override.
        password: Optional raw password override.
        is_active: Whether the account is active.

    Returns:
        Persisted account object.

    Raises:
        ValueError: If a caller explicitly supplies an invalid identifier.
    """
    index = next(_sequence)

    return User.objects.db_manager("default").create_user(
        f"factory-user-{index}" if username is None else username,
        f"factory-user-{index}@localforge.invalid" if email is None else email,
        secrets.token_urlsafe(24) if password is None else password,
        is_active=is_active,
    )


def build_login_throttle_event(
    *,
    bucket: str = "opaque-bucket",
    request_id: str | None = None,
    occurred_at: datetime | None = None,
) -> LoginThrottleEvent:
    """Build one valid unsaved login-admission event.

    Supplies opaque identifiers and accepts an optional deterministic timestamp for direct model
    behavior tests.

    Arguments:
        bucket: Opaque throttle bucket.
        request_id: Optional request identifier override.
        occurred_at: Optional event timestamp.

    Returns:
        Valid unsaved login event.

    Raises:
        None.
    """
    return LoginThrottleEvent(
        bucket=bucket,
        request_id=str(uuid.uuid4()) if request_id is None else request_id,
        occurred_at=occurred_at or timezone.now(),
    )


def build_activation_token(
    *,
    subject_id: uuid.UUID | None = None,
    digest: str | None = None,
) -> ActivationToken:
    """Build one valid unsaved activation-token record.

    Uses immutable identifiers and digest-shaped text without creating bearer material or touching
    the database.

    Arguments:
        subject_id: Optional immutable account identifier.
        digest: Optional digest override.

    Returns:
        Valid unsaved activation token.

    Raises:
        None.
    """
    return ActivationToken(
        subject_id=subject_id or uuid.uuid4(),
        digest=secrets.token_hex(32) if digest is None else digest,
    )


def build_password_reset_token(
    *,
    subject_id: uuid.UUID | None = None,
    digest: str | None = None,
) -> PasswordResetToken:
    """Build one valid unsaved password-reset record.

    Supplies only safe persisted state and avoids reconstructable credential values.
    The object remains unsaved so isolated tests never need a database.

    Arguments:
        subject_id: Optional immutable account identifier.
        digest: Optional digest override.

    Returns:
        Valid unsaved password-reset token.

    Raises:
        None.
    """
    return PasswordResetToken(
        subject_id=subject_id or uuid.uuid4(),
        digest=secrets.token_hex(32) if digest is None else digest,
    )


def build_username_reset_token(
    *,
    subject_id: uuid.UUID | None = None,
    digest: str | None = None,
) -> UsernameResetToken:
    """Build one valid unsaved username-reset record.

    Supplies only safe persisted state and avoids reconstructable credential values.
    The object remains unsaved so isolated tests never need a database.

    Arguments:
        subject_id: Optional immutable account identifier.
        digest: Optional digest override.

    Returns:
        Valid unsaved username-reset token.

    Raises:
        None.
    """
    return UsernameResetToken(
        subject_id=subject_id or uuid.uuid4(),
        digest=secrets.token_hex(32) if digest is None else digest,
    )
