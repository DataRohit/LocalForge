"""Unit tests for the accounts application configuration.

Verifies the stable application identity used by migrations and authentication model references
without loading any external service.
"""

import pytest

from accounts.apps import AccountsConfig


@pytest.mark.unit
def test_accounts_configuration_keeps_its_stable_identity() -> None:
    """Keep the application import path and migration label fixed.

    Compares the complete identity fields against the values referenced by migrations and settings.
    This keeps generated relation labels stable across the project.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the application identity or default key type changes.
    """
    assert AccountsConfig.name == "accounts"
    assert AccountsConfig.label == "accounts"
    assert AccountsConfig.default_auto_field == "django.db.models.BigAutoField"
