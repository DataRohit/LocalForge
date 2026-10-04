"""Unit tests for testing-only settings.

Keeps the headless test configuration independent of operator debug values.
Verifies the fixed override directly from the public module.
"""

import pytest

from config.settings import testing


@pytest.mark.unit
def test_testing_settings_force_debug_off() -> None:
    """Disable debug behavior in every test mode.

    Reads the public environment module value that both host and container execution import.
    No environment file may re-enable debug behavior.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If testing can inherit debug mode.
    """
    assert testing.DEBUG is False
