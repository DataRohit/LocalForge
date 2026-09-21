"""Unit tests for the account manager.

Exercises case-insensitive natural-key lookup and valid-by-default account construction while
substituting the model save boundary.
"""

from unittest.mock import patch

import pytest

from accounts.managers import UserManager
from accounts.models import User
from tests.factories import build_user


def _manager() -> UserManager:
    """Build one manager bound to the project user model.

    Supplies the model binding Django normally installs during application setup so direct manager
    tests use the public methods without a database.

    Arguments:
        None.

    Returns:
        Bound user manager.

    Raises:
        None.
    """
    manager = UserManager()
    manager.model = User
    return manager


@pytest.mark.unit
def test_natural_key_lookup_is_case_insensitive() -> None:
    """Look up usernames with the case-insensitive identifier contract.

    Replaces only the queryset lookup and verifies the manager emits the exact field lookup.
    The test remains independent of database collation.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If lookup becomes case-sensitive or uses another field.
    """
    manager = _manager()
    account = build_user()

    with patch.object(manager, "get", return_value=account) as get:
        result = manager.get_by_natural_key("MixedCase")

    assert result is account
    get.assert_called_once_with(username__iexact="MixedCase")


@pytest.mark.unit
def test_create_user_builds_an_inactive_valid_account() -> None:
    """Construct an ordinary account with normalized identity and safe defaults.

    Substitutes model persistence while retaining password hashing and manager field decisions.
    This isolates construction defaults from database enforcement.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the account is active, privileged, unnormalized, or unhashed.
    """
    manager = _manager()

    with patch.object(User, "save") as save:
        account = manager.create_user(
            "manager-user",
            " Manager-User@LOCALFORGE.Invalid ",
            "valid-manager-password",
        )

    assert account.email == "manager-user@localforge.invalid"
    assert account.is_active is False
    assert account.is_staff is False
    assert account.is_superuser is False
    assert account.check_password("valid-manager-password")
    save.assert_called_once_with(using=None)


@pytest.mark.unit
def test_create_superuser_builds_an_active_fully_privileged_account() -> None:
    """Construct a superuser with every required permission default.

    Substitutes model persistence while exercising the successful manager path, proving it
    normalizes the address, hashes the password, and forces all three privileged flags.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the successful superuser path omits a flag or identity transformation.
    """
    manager = _manager()

    with patch.object(User, "save") as save:
        account = manager.create_superuser(
            "manager-root",
            " Manager-Root@LOCALFORGE.Invalid ",
            "valid-root-password",
        )

    assert account.email == "manager-root@localforge.invalid"
    assert account.is_active is True
    assert account.is_staff is True
    assert account.is_superuser is True
    assert account.check_password("valid-root-password")
    save.assert_called_once_with(using=None)
