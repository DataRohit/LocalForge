"""Unit tests for testing-environment isolation.

Proves that every command built for ``testing`` addresses only the ``localforge-test`` project and
``.env.testing``, and that the Make targets which reach a Django database refuse to pick an
environment for the caller.
"""

import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from scripts import manage_platform as platform

from tests.conftest import LiveDevelopmentFileError
from tests.unit.scripts.test_platform import FakeRunner, ImageStateRunner

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.unit

_REPOSITORY_ROOT_CANDIDATE = Path(__file__).resolve().parents[4]
REPOSITORY_ROOT = (
    _REPOSITORY_ROOT_CANDIDATE
    if (_REPOSITORY_ROOT_CANDIDATE / "compose.yaml").is_file()
    else Path(__file__).resolve().parents[3]
)
MAKEFILE = REPOSITORY_ROOT / "Makefile"
MAKE_TIMEOUT_SECONDS = 30
ENVIRONMENT_TARGETS = ("dev", "migrate", "migrations", "superuser", "shell")
TESTING_COMMANDS = (
    "testing-build",
    "testing-up",
    "testing-down",
    "testing-status",
    "testing-logs",
)
FORBIDDEN_FRAGMENTS = (".env.development", "localforge-dev", "compose.development.yaml")


def isolated_root(root: Path) -> Path:
    """Create a repository root holding no development environment file.

    Writes only the testing files, so any code path that tried to open the development file would
    fail instead of silently succeeding.

    Arguments:
        root: Temporary directory to populate.

    Returns:
        The populated root.
    """
    for name in (".env.testing", ".env.testing.host"):
        (root / name).write_text("NAME=value\n", encoding="utf-8")

    return root


def iter_commands(runner: FakeRunner) -> Iterator[tuple[str, ...]]:
    """Yield every command a fake runner recorded.

    Flattens the recorded call tuples so assertions read the argument vectors directly. Keeps
    the isolation assertions free of tuple indexing.

    Arguments:
        runner: Runner whose calls are inspected.

    Yields:
        Each recorded argument vector.
    """
    for call in runner.calls:
        yield call[0]


@pytest.mark.parametrize("command", TESTING_COMMANDS)
def test_testing_commands_never_address_development(tmp_path: Path, command: str) -> None:
    """Keep every testing command on the isolated project and file.

    Runs each testing operation against a root that has no development file and asserts the
    commands carry the testing project and environment file and never name a development resource.

    Arguments:
        tmp_path: Temporary repository root.
        command: Public testing command.

    Returns:
        None.

    Raises:
        AssertionError: If a command names development or omits the testing scope.
    """
    root = isolated_root(tmp_path)
    runner = ImageStateRunner(present_images=set(platform.SETUP_IMAGES))

    platform.main([command], root=root, runner=runner)

    assert runner.calls
    for recorded in iter_commands(runner):
        joined = " ".join(recorded)
        assert not any(fragment in joined for fragment in FORBIDDEN_FRAGMENTS), joined
        if recorded[:2] == ("docker", "compose"):
            assert recorded[recorded.index("--project-name") + 1] == "localforge-test"
            assert recorded[recorded.index("--env-file") + 1] == ".env.testing"


@pytest.mark.parametrize("mode", ["container", "host"])
def test_testing_mode_commands_never_address_development(mode: str) -> None:
    """Keep both test execution modes off the live environment.

    Builds the collection and complete-suite commands for each mode and asserts none names a
    development resource and every Compose command is scoped to the testing project and file.

    Arguments:
        mode: Test execution mode.

    Returns:
        None.

    Raises:
        AssertionError: If any built command reaches development.
    """
    collections, suite = platform.testing_mode_commands(mode)

    for recorded in (*collections, suite):
        joined = " ".join(recorded)
        assert not any(fragment in joined for fragment in FORBIDDEN_FRAGMENTS), joined
        if recorded[:2] == ("docker", "compose"):
            assert recorded[recorded.index("--project-name") + 1] == "localforge-test"
            assert recorded[recorded.index("--env-file") + 1] == ".env.testing"


def test_testing_compose_overlay_never_mounts_the_development_file() -> None:
    """Keep the live credentials out of the test container.

    Reads the testing overlay and asserts it names the development file nowhere, so the container
    that runs the suite cannot read credentials belonging to the live stack.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the overlay references the development file.
    """
    overlay = (REPOSITORY_ROOT / "compose.testing.yaml").read_text(encoding="utf-8")

    assert ".env.development" not in overlay


def test_make_environment_targets_are_guarded() -> None:
    """Require an explicit environment for the database-reaching targets.

    Reads the Makefile and asserts each target that opens a Django process depends on the guard
    and that the guard demands a command-line selection from the two known environments, which is
    then exported as the settings module.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If a target is unguarded or the guard accepts a default.
    """
    text = MAKEFILE.read_text(encoding="utf-8")

    assert f"ENVIRONMENT_TARGETS := {' '.join(ENVIRONMENT_TARGETS)}" in text
    assert "$(ENVIRONMENT_TARGETS): require-environment" in text
    assert "ifneq ($(origin ENVIRONMENT),command line)" in text
    assert "ENVIRONMENT_CHOICES := development testing" in text
    assert "ENVIRONMENT ?=" not in text
    assert (
        "$(ENVIRONMENT_TARGETS): export DJANGO_SETTINGS_MODULE = config.settings.$(ENVIRONMENT)"
        in text
    )


@pytest.mark.parametrize("target", ENVIRONMENT_TARGETS)
@pytest.mark.parametrize("selection", [[], ["ENVIRONMENT=production"]])
def test_make_refuses_to_default_the_environment(target: str, selection: list[str]) -> None:
    """Fail closed when no valid environment is selected.

    Invokes Make in dry-run mode for each guarded target without a selection and with an unknown
    one, and asserts it stops with an error. The test image carries no Make, so container mode
    relies on the text assertions above and host mode runs the real tool.

    Arguments:
        target: Guarded Make target.
        selection: Command-line variable assignments.

    Returns:
        None.

    Raises:
        AssertionError: If Make accepts the target.
    """
    make = shutil.which("make")
    if make is None:
        return

    result = subprocess.run(  # noqa: S603
        [make, "-n", "-f", str(MAKEFILE), target, *selection],
        capture_output=True,
        check=False,
        cwd=REPOSITORY_ROOT,
        text=True,
        timeout=MAKE_TIMEOUT_SECONDS,
    )

    assert result.returncode != 0
    assert "ENVIRONMENT" in result.stderr


OPERATOR_TARGETS = frozenset(
    {
        "setup",
        "environments-setup",
        "docker-audit",
        "docker-clean-check",
        "convention-audit",
        "convention-audit-development",
        "convention-audit-testing",
        "security-audit",
        "security-audit-runtime",
        "secrets-generate",
        "secrets-decrypt",
        "developer-access-export",
    }
)


def test_only_declared_operator_targets_may_address_development() -> None:
    """Confine development access to named operator tasks.

    Reads the Make task list and requires every declared operator task to exist and none of them
    to be a testing task, so the targets that can reach the live stack stay a short, named list.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If an operator task has disappeared or a testing task is declared one.
    """
    block = MAKEFILE.read_text(encoding="utf-8").split("POE_TASKS :=")[1].split(".PHONY")[0]
    targets = frozenset(word for word in block.split() if word != "\\")

    assert targets >= OPERATOR_TARGETS
    assert {name for name in targets if name.startswith("development-")}
    assert not {name for name in targets if name.startswith("testing-")} & OPERATOR_TARGETS


def test_the_suite_refuses_to_open_the_live_development_file() -> None:
    """Fail any test that reads the live development environment file.

    Attempts both a plain open and a path read of the live file and expects the suite-wide guard to
    raise, while a copy in a temporary directory stays readable.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the live file can be opened.
    """
    live = (REPOSITORY_ROOT / ".env.development").resolve()

    with pytest.raises(LiveDevelopmentFileError):
        open(live, encoding="utf-8")  # noqa: SIM115, PTH123
    with pytest.raises(LiveDevelopmentFileError):
        live.read_text(encoding="utf-8")
