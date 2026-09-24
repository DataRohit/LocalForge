"""Unit tests for account credential verification policy.

Covers locked account revalidation at the credential-policy interface, including
disappearance, state drift, password drift, and prepared hash replacement.
"""

from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from rest_framework.exceptions import AuthenticationFailed

from accounts.credentials import AuthenticatedCredentialSnapshot, lock_authenticated_account
from accounts.models import User

VERIFIED_ENCODING = "encoded-password"
CHANGED_ENCODING = "changed-password"
PREFERRED_ENCODING = "preferred-encoded-password"


def snapshot(
    *,
    active: bool = True,
    encoded_password: str = VERIFIED_ENCODING,
    replacement: str | None = None,
) -> AuthenticatedCredentialSnapshot:
    """Build one immutable credential snapshot.

    Supplies explicit security state to locked-revalidation tests without constructing a persisted
    account or depending on Django's password hashers.

    Arguments:
        active: Active state observed during lock-free verification.
        encoded_password: Password encoding observed during verification.
        replacement: Preferred encoding prepared before the account lock.

    Returns:
        Credential snapshot for locked revalidation.
    """
    return AuthenticatedCredentialSnapshot(
        account_id=uuid4(),
        encoded_password=encoded_password,
        is_active=active,
        replacement_encoded_password=replacement,
    )


@pytest.mark.unit
def test_locked_credential_revalidation_rejects_a_missing_account() -> None:
    """Reject an account deleted after lock-free verification.

    Makes the authoritative locked lookup disappear and requires the public authentication failure
    rather than leaking the model exception.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If disappearance escapes through another exception.
    """
    manager = MagicMock()
    manager.using.return_value.select_for_update.return_value.get.side_effect = User.DoesNotExist

    with (
        patch.object(User, "objects", manager),
        pytest.raises(AuthenticationFailed),
    ):
        lock_authenticated_account(snapshot())


@pytest.mark.unit
@pytest.mark.parametrize(
    ("account_active", "snapshot_active", "account_password", "snapshot_password"),
    [
        (False, True, VERIFIED_ENCODING, VERIFIED_ENCODING),
        (False, False, VERIFIED_ENCODING, VERIFIED_ENCODING),
        (True, True, CHANGED_ENCODING, VERIFIED_ENCODING),
    ],
)
def test_locked_credential_revalidation_rejects_state_drift(
    *,
    account_active: bool,
    snapshot_active: bool,
    account_password: str,
    snapshot_password: str,
) -> None:
    """Reject every account state that differs from verified credentials.

    Exercises active-state change, retained inactivity, and password replacement independently so
    no changed security field can survive locked revalidation.

    Arguments:
        account_active: Current locked active state.
        snapshot_active: Previously verified active state.
        account_password: Current locked password encoding.
        snapshot_password: Previously verified password encoding.

    Returns:
        None.

    Raises:
        AssertionError: If any changed state is accepted.
    """
    account = MagicMock(is_active=account_active, password=account_password)
    manager = MagicMock()
    manager.using.return_value.select_for_update.return_value.get.return_value = account

    with (
        patch.object(User, "objects", manager),
        pytest.raises(AuthenticationFailed),
    ):
        lock_authenticated_account(
            snapshot(
                active=snapshot_active,
                encoded_password=snapshot_password,
            )
        )


@pytest.mark.unit
@pytest.mark.parametrize("replacement", [None, PREFERRED_ENCODING])
def test_locked_credential_revalidation_applies_only_a_prepared_replacement(
    replacement: str | None,
) -> None:
    """Return exact locked state and apply only a prepared preferred encoding.

    Keeps successful issuance independent from fresh password work under the lock while preserving
    the account unchanged when verification prepared no replacement.

    Arguments:
        replacement: Preferred encoding prepared before the account lock.

    Returns:
        None.

    Raises:
        AssertionError: If the account is replaced, saved unnecessarily, or saved incorrectly.
    """
    account = MagicMock(is_active=True, password=VERIFIED_ENCODING)
    manager = MagicMock()
    manager.using.return_value.select_for_update.return_value.get.return_value = account

    with patch.object(User, "objects", manager):
        observed = lock_authenticated_account(snapshot(replacement=replacement))

    assert observed is account
    if replacement is None:
        account.save.assert_not_called()
    else:
        assert account.password == replacement
        account.save.assert_called_once_with(
            using="default",
            update_fields=["password"],
        )
