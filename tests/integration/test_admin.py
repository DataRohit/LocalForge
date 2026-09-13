"""Integration tests for the administration interface.

Covers the administration login page end to end through the test client, spanning URL routing,
the view layer, and template rendering.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from django.test import Client


@pytest.mark.integration
@pytest.mark.django_db
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
