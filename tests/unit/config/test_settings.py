"""Unit tests for the settings module.

Covers the configuration values other parts of the project depend on by name, so a rename or an
accidental change is caught here rather than at runtime.
"""

import pytest

from config import settings


@pytest.mark.unit
def test_entry_point_settings_reference_the_project_modules() -> None:
    """Point the framework at the project's own modules.

    Confirms the root URL configuration and the WSGI application path resolve to this project
    rather than to a generated default left over from scaffolding.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If either setting does not name the project module.
    """
    assert settings.ROOT_URLCONF == "config.urls"
    assert settings.WSGI_APPLICATION == "config.wsgi.application"


@pytest.mark.unit
def test_installed_applications_include_the_admin_and_its_dependencies() -> None:
    """Register the admin and the applications it requires.

    Confirms the admin is installed together with the authentication and content type
    applications it depends on, since the admin fails to load without them.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If any required application is absent.
    """
    required = {
        "django.contrib.admin",
        "django.contrib.auth",
        "django.contrib.contenttypes",
        "django.contrib.sessions",
    }

    assert required.issubset(set(settings.INSTALLED_APPS))


@pytest.mark.unit
def test_timezone_support_is_enabled() -> None:
    """Store datetimes as timezone-aware values.

    Confirms timezone support is on, because turning it off later silently reinterprets stored
    values and is effectively irreversible once data exists.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If timezone support is disabled.
    """
    assert settings.USE_TZ is True
