"""Django management entry point.

Runs management commands against the project, defaulting to the isolated testing settings so a
command issued without an explicit selection can never address the live development environment.
"""

import os
import sys


def main() -> None:
    """Run one management command.

    Selects the testing settings module when the environment names none, then hands the command
    line to Django, importing the runner late so the settings selection is in place first.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        ImportError: If Django is not installed in the active environment.
    """
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.testing")
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
