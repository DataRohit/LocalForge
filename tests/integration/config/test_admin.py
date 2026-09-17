"""Integration tests for the administration interface.

Covers the administration login page end to end through the test client, spanning URL routing,
the view layer, and template rendering.
"""

from __future__ import annotations

import uuid
from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest

from accounts.models import User

if TYPE_CHECKING:
    from django.test import Client

PASSWORD = "Correct-Horse-Battery-Staple-32"  # noqa: S105


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.django_db(databases=["default", "replica"])
def test_admin_login_page_renders(client: Client) -> None:
    """Serve the administration login page.

    Requests the login page through the test client and confirms it responds successfully using
    the expected template, which exercises routing, the view, and template discovery together.

    Arguments:
        client: Django test client supplied by the test framework.

    Returns:
        None.

    Raises:
        AssertionError: If the page does not respond successfully or uses another template.
    """
    response = client.get("/admin/login/")

    assert response.status_code == HTTPStatus.OK
    assert "admin/login.html" in [template.name for template in response.templates]


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_admin_edit_preserves_an_inactive_account(admin_client: Client) -> None:
    """Save an ordinary administration edit without activating the account.

    Posts the existing inactive values through the public change form and verifies the ordinary
    administration save path retains the account's activation state.

    Arguments:
        admin_client: Authenticated administration client supplied by the test framework.

    Returns:
        None.

    Raises:
        AssertionError: If the edit fails or changes the account's activation state.
    """
    account = User.objects.create_user(
        "inactive-admin-edit",
        "inactive-admin-edit@localforge.invalid",
    )

    response = admin_client.post(
        f"/admin/accounts/user/{account.pk}/change/",
        {
            "username": account.username,
            "email": account.email,
            "_save": "Save",
        },
    )
    account.refresh_from_db(using="default")

    assert response.status_code == HTTPStatus.FOUND
    assert account.is_active is False


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_admin_add_creates_an_inactive_account(admin_client: Client) -> None:
    """Create an account through the administration add form.

    Posts the complete public administration form and verifies the existing creation path still
    persists an inactive account with a usable password after edit saves gained locking behavior.

    Arguments:
        admin_client: Authenticated administration client supplied by the test framework.

    Returns:
        None.

    Raises:
        AssertionError: If account creation fails or changes its established account defaults.
    """
    suffix = uuid.uuid4().hex
    username = f"admin-created-{suffix}"
    email = f"admin-created-{suffix}@localforge.invalid"

    response = admin_client.post(
        "/admin/accounts/user/add/",
        {
            "username": username,
            "email": email,
            "usable_password": "true",
            "password1": PASSWORD,
            "password2": PASSWORD,
            "_save": "Save",
        },
    )
    account = User.objects.using("default").get(username=username)

    assert response.status_code == HTTPStatus.FOUND
    assert account.email == email
    assert account.is_active is False
    assert account.check_password(PASSWORD)
