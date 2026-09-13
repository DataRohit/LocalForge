"""Unit tests for the root URL configuration.

Covers the routes the project exposes, confirming each one resolves to the expected view so a
change to the URL table is deliberate rather than accidental.
"""

import pytest
from django.urls import resolve


@pytest.mark.unit
def test_admin_route_resolves() -> None:
    """Resolve the administration route.

    Confirms the admin is mounted at its expected prefix, which is the only route the project
    exposes before the application surface is built.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the admin route does not resolve to its expected pattern.
    """
    assert resolve("/admin/").route == "admin/"
