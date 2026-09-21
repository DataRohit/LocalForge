"""Unit tests for account administration behavior.

Exercises permission-field locking and the activation-aware save seam without reaching the
database or the administration HTTP surface.
"""

from types import SimpleNamespace
from typing import TYPE_CHECKING, cast
from unittest.mock import patch

import pytest
from django.contrib.admin import AdminSite

from accounts.admin import AccountAdmin
from accounts.models import User
from tests.factories import build_user

if TYPE_CHECKING:
    from django.forms import ModelForm
    from django.http import HttpRequest


@pytest.mark.unit
def test_existing_account_edits_use_the_activation_aware_save_service() -> None:
    """Route existing-account changes through activation transition handling.

    Calls the administration save seam directly and verifies it delegates the populated account
    once rather than using the framework's ordinary model save.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If an existing account bypasses activation-aware persistence.
    """
    account = build_user()
    administrator = AccountAdmin(User, AdminSite())
    request = cast("HttpRequest", SimpleNamespace(user=SimpleNamespace(is_superuser=True)))
    form = cast("ModelForm[User]", object())

    with patch("accounts.admin.save_account_from_admin") as save_account:
        administrator.save_model(request, account, form, change=True)

    save_account.assert_called_once_with(account)
