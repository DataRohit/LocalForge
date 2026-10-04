"""Unit tests for environment-file selection.

Exercises the settings package's public loader against a temporary repository root without
touching a project or machine credential file.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest

from config import settings as settings_package

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.unit
def test_environment_loader_reads_only_the_selected_missing_configuration(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Load the selected environment file when process configuration is absent.

    Redirects the repository root to a temporary manifest and verifies the public loader returns
    that path and populates the missing marker.

    Arguments:
        monkeypatch: Fixture isolating process environment and module state.
        tmp_path: Temporary repository root containing the probe environment file.

    Returns:
        None.

    Raises:
        AssertionError: If selection, loading, or returned evidence differs.
    """
    canary = "unit-package-" + "secret"
    environment_file = tmp_path / ".env.development"
    environment_file.write_text(f"DJANGO_SECRET_KEY={canary}\n", encoding="utf-8")
    monkeypatch.setattr(settings_package, "REPOSITORY_ROOT", tmp_path)
    monkeypatch.setenv("DJANGO_SETTINGS_MODULE", "config.settings.development")
    monkeypatch.delenv("DJANGO_SECRET_KEY", raising=False)

    loaded = settings_package.load_environment_file()

    assert loaded == environment_file
    assert os.environ["DJANGO_SECRET_KEY"] == canary
