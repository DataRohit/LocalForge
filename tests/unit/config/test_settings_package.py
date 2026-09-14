"""Unit tests for the environment file loader.

Covers when the settings package reads an environment file and when it deliberately does not, so a
container keeps the configuration Compose gave it and a partly configured process still fails
naming the variable it is missing.
"""

import os
import secrets
from typing import TYPE_CHECKING
from unittest import mock

import pytest

from config import settings as settings_package

if TYPE_CHECKING:
    from pathlib import Path

TESTING_MODULE = "config.settings.testing"


@pytest.mark.unit
def test_a_configured_process_loads_no_file() -> None:
    """Leave a configured process alone.

    Confirms a process that already carries configuration reads no file, which keeps a container's
    own values authoritative when a source tree carrying an environment file is mounted into it,
    and keeps a missing variable a failure rather than a value borrowed from another environment.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If a file is reported as loaded.
    """
    environment = {
        "DJANGO_SETTINGS_MODULE": TESTING_MODULE,
        "DJANGO_SECRET_KEY": secrets.token_urlsafe(16),
    }

    with mock.patch.dict(os.environ, environment, clear=True):
        assert settings_package.load_environment_file() is None


@pytest.mark.unit
def test_an_unknown_settings_module_loads_no_file() -> None:
    """Load nothing for a module the table does not name.

    Confirms a settings module outside the table resolves to no file, which is what happens when a
    tool imports the base module directly and must rely on the process environment.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If a file is reported as loaded.
    """
    environment = {"DJANGO_SETTINGS_MODULE": "config.settings.base"}

    with mock.patch.dict(os.environ, environment, clear=True):
        assert settings_package.load_environment_file() is None


@pytest.mark.unit
def test_no_settings_module_selected_loads_no_file() -> None:
    """Load nothing when no module is selected.

    Confirms an empty selection resolves to no file rather than guessing an environment, since a
    wrong guess would point a process at another environment's services.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If a file is reported as loaded.
    """
    with mock.patch.dict(os.environ, {}, clear=True):
        assert settings_package.load_environment_file() is None


@pytest.mark.unit
def test_an_absent_file_loads_nothing(tmp_path: Path) -> None:
    """Tolerate a missing environment file.

    Confirms a named file that does not exist resolves to nothing, so a checkout without generated
    environment files fails on the first missing variable rather than on a missing file.

    Arguments:
        tmp_path: Temporary directory standing in for the repository root.

    Returns:
        None.

    Raises:
        AssertionError: If a file is reported as loaded.
    """
    with (
        mock.patch.object(settings_package, "REPOSITORY_ROOT", tmp_path),
        mock.patch.dict(os.environ, {"DJANGO_SETTINGS_MODULE": TESTING_MODULE}, clear=True),
    ):
        assert settings_package.load_environment_file() is None


@pytest.mark.unit
def test_a_present_file_is_read_without_overwriting_the_process(tmp_path: Path) -> None:
    """Fill the variables an unconfigured process is missing.

    Confirms the named file supplies absent variables and leaves anything the process already
    carries untouched, which is what a host-mode run relies on to reach the published ports.

    Arguments:
        tmp_path: Temporary directory standing in for the repository root.

    Returns:
        None.

    Raises:
        AssertionError: If the file is not read, or if it overwrites an existing value.
    """
    environment_file = tmp_path / ".env.testing.host"
    environment_file.write_text(
        "LOCALFORGE_PROBE_ABSENT=from-file\nLOCALFORGE_PROBE_PRESENT=from-file\n",
        encoding="utf-8",
    )

    environment = {
        "DJANGO_SETTINGS_MODULE": TESTING_MODULE,
        "LOCALFORGE_PROBE_PRESENT": "from-process",
    }

    with (
        mock.patch.object(settings_package, "REPOSITORY_ROOT", tmp_path),
        mock.patch.dict(os.environ, environment, clear=True),
    ):
        loaded = settings_package.load_environment_file()

        assert loaded == environment_file
        assert os.environ["LOCALFORGE_PROBE_ABSENT"] == "from-file"
        assert os.environ["LOCALFORGE_PROBE_PRESENT"] == "from-process"


@pytest.mark.unit
def test_every_environment_module_has_an_entry() -> None:
    """Name a file for each environment module.

    Confirms the table covers both environment modules, because a module missing from it would
    silently fall back to whatever the process environment already held.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If either environment module is absent from the table.
    """
    assert set(settings_package.ENVIRONMENT_FILES) == {
        "config.settings.development",
        TESTING_MODULE,
    }
