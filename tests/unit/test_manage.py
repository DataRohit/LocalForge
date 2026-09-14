"""Unit tests for the management entry point.

Covers argument dispatch through the Django management framework and the script guard that runs
when the module is executed directly rather than imported.
"""

import os
import runpy
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

import manage


@pytest.mark.unit
def test_main_dispatches_arguments_to_django() -> None:
    """Forward the received arguments to the management framework.

    Confirms that the entry point performs no argument handling of its own and hands the raw
    argument vector to Django unchanged.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the arguments are not forwarded exactly once and unchanged.
    """
    arguments = ["src/manage.py", "check"]

    with (
        patch.object(sys, "argv", arguments),
        patch("django.core.management.execute_from_command_line") as execute,
    ):
        manage.main()

    execute.assert_called_once_with(arguments)


@pytest.mark.unit
def test_the_default_settings_module_is_the_development_environment() -> None:
    """Select the development settings when none is named.

    Confirms an invocation with no settings module selected falls back to the development module by
    name, since a typo in that string would otherwise surface only outside the suite, where the
    variable is always already set.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the fallback does not name the development settings module.
    """
    environment = dict(os.environ)
    environment.pop("DJANGO_SETTINGS_MODULE", None)

    with (
        patch.dict(os.environ, environment, clear=True),
        patch.object(sys, "argv", ["src/manage.py", "check"]),
        patch("django.core.management.execute_from_command_line"),
    ):
        manage.main()

        assert os.environ["DJANGO_SETTINGS_MODULE"] == "config.settings.development"


@pytest.mark.unit
def test_script_guard_invokes_main() -> None:
    """Run the entry point through its script guard.

    Executes the module under the name Python assigns to a directly executed script, which
    exercises the guard that an ordinary import never reaches.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If executing the module as a script does not dispatch the arguments.
    """
    arguments = ["src/manage.py", "check"]
    module_path = Path(manage.__file__)

    with (
        patch.object(sys, "argv", arguments),
        patch("django.core.management.execute_from_command_line") as execute,
    ):
        runpy.run_path(str(module_path), run_name="__main__")

    execute.assert_called_once_with(arguments)
