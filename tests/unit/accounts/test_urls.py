"""Unit tests for the fixed account route table.

Keeps the closed application surface mechanically tied to the exact route names and paths without
dispatching a request.
"""

import pytest

from accounts.urls import urlpatterns


@pytest.mark.unit
def test_account_routes_match_the_fixed_surface() -> None:
    """Publish only the documented account and credential routes.

    Compares the complete path/name mapping so an added, missing, or renamed endpoint fails before
    schema generation.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the fixed route table drifts.
    """
    observed = {(str(pattern.pattern), pattern.name) for pattern in urlpatterns}

    assert observed == {
        ("users/", "user-registration"),
        ("users/me/", "user-profile"),
        ("users/resend_activation/", "activation-resend"),
        ("users/set_password/", "password-change"),
        ("users/reset_password/", "password-reset"),
        ("users/reset_password_confirm/", "password-reset-confirm"),
        ("users/set_username/", "username-change"),
        ("users/reset_username/", "username-reset"),
        ("users/reset_username_confirm/", "username-reset-confirm"),
        ("jwt/create/", "jwt-create"),
        ("jwt/refresh/", "jwt-refresh"),
        ("jwt/verify/", "jwt-verify"),
        ("token/login/", "token-login"),
        ("token/logout/", "token-logout"),
    }
