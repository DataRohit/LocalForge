"""Unit tests for the Django project package.

Verifies the package exports the one Celery application object used by Django startup and worker
commands.
"""

import pytest

from config import celery_app
from config.celery import app


@pytest.mark.unit
def test_project_package_exports_the_configured_celery_application() -> None:
    """Keep Django's Celery autodiscovery export bound to the configured app.

    Compares object identity so importing the package cannot construct or expose a second task
    application.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the package exports another Celery instance.
    """
    assert celery_app is app
