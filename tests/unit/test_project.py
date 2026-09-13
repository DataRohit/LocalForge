import runpy
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from django.core.handlers.asgi import ASGIHandler
from django.core.handlers.wsgi import WSGIHandler
from django.urls import resolve

import manage
from config import asgi, settings, wsgi


@pytest.mark.unit
def test_project_configuration() -> None:
    assert settings.DEBUG is True
    assert settings.ROOT_URLCONF == "config.urls"
    assert settings.WSGI_APPLICATION == "config.wsgi.application"
    assert settings.DATABASES["default"]["ENGINE"] == "django.db.backends.sqlite3"
    assert resolve("/admin/").route == "admin/"
    assert isinstance(asgi.application, ASGIHandler)
    assert isinstance(wsgi.application, WSGIHandler)


@pytest.mark.unit
def test_manage_main_dispatches_arguments() -> None:
    arguments = ["src/manage.py", "check"]
    with (
        patch.object(sys, "argv", arguments),
        patch("django.core.management.execute_from_command_line") as execute,
    ):
        manage.main()
    execute.assert_called_once_with(arguments)


@pytest.mark.unit
def test_manage_script_entry_point() -> None:
    arguments = ["src/manage.py", "check"]
    manage_path = Path(manage.__file__)
    with (
        patch.object(sys, "argv", arguments),
        patch("django.core.management.execute_from_command_line") as execute,
    ):
        runpy.run_path(str(manage_path), run_name="__main__")
    execute.assert_called_once_with(arguments)
