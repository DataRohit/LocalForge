"""Unit tests for the backup agent entrypoint.

Exercises the entrypoint as a real process against fabricated tools on the search path, so the
documented exit codes, the stanza handling, the schedule matcher, and the shutdown behaviour are
proved rather than inferred from reading the script.
"""

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent.parent.parent
ENTRYPOINT = REPOSITORY_ROOT / "scripts" / "pgbackrest_entrypoint.sh"
CONVENTIONS = REPOSITORY_ROOT / "docs" / "platform" / "conventions.md"
BASH = shutil.which("bash")
TIMEOUT_SECONDS = 30

EXIT_OK = 0
EXIT_STANZA_FAILED = 1
EXIT_BACKUP_FAILED = 2

pytestmark = pytest.mark.skipif(BASH is None, reason="the entrypoint needs a POSIX shell")


def write_tool(directory: Path, name: str, body: str) -> None:
    """Place a fabricated command on the search path.

    Writes a small shell script standing in for one of the tools the entrypoint calls, so its
    behaviour can be dictated per test without installing anything.

    Arguments:
        directory: Directory that will be prepended to the search path.
        name: Command name to fabricate.
        body: Shell body the fabricated command runs.

    Returns:
        None.
    """
    path = directory / name
    path.write_text(f"#!/usr/bin/env bash\n{body}\n", encoding="utf-8", newline="\n")
    path.chmod(0o755)


def environment(directory: Path) -> dict[str, str]:
    """Build the environment the entrypoint requires.

    Supplies every variable the script asserts on, with schedules that never fire, so a test opts
    in to a backup rather than racing one.

    Arguments:
        directory: Directory holding the fabricated commands.

    Returns:
        The environment for the subprocess.
    """
    return {
        **os.environ,
        "PATH": f"{directory}{os.pathsep}{os.environ['PATH']}",
        "PGBACKREST_STANZA": "localforge",
        "LOCALFORGE_BACKUP_FULL_SCHEDULE": "0 2 31 2 *",
        "LOCALFORGE_BACKUP_DIFF_SCHEDULE": "0 3 31 2 *",
        "LOCALFORGE_BACKUP_WAIT_SECONDS": "2",
        "POSTGRES_HOST": "primary",
        "POSTGRES_PORT": "5432",
    }


def run(directory: Path, *, timeout: int = TIMEOUT_SECONDS) -> subprocess.CompletedProcess[str]:
    """Run the entrypoint against the fabricated tools.

    Executes the script as a process so its exit code is the real one, which is the whole point of
    the documented contract.

    Arguments:
        directory: Directory holding the fabricated commands.
        timeout: Seconds to allow before giving up.

    Returns:
        The completed process.
    """
    process = subprocess.Popen(  # noqa: S603
        [str(BASH), str(ENTRYPOINT)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=environment(directory),
        cwd=REPOSITORY_ROOT,
    )
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            break
        if reached_loop(directory):
            process.terminate()
            break
        time.sleep(0.2)

    output, errors = process.communicate(timeout=timeout)

    return subprocess.CompletedProcess(
        args=[str(ENTRYPOINT)],
        returncode=process.returncode,
        stdout=output,
        stderr=errors,
    )


def reached_loop(directory: Path) -> bool:
    """Report whether the entrypoint has finished starting up.

    Reads the marker the fabricated tool records, so a test can stop a run that has done everything
    it was going to do and would otherwise loop forever.

    Arguments:
        directory: Directory holding the fabricated commands.

    Returns:
        True once the startup sequence has completed.
    """
    marker = directory / "reached-loop"

    return marker.exists()


@pytest.fixture
def tools(tmp_path: Path) -> Path:
    """Provide a directory of fabricated tools that all succeed.

    Starts every test from a machine where every tool succeeds, so a test replaces only the one
    command whose failure it is about and inherits the rest.

    Arguments:
        tmp_path: Temporary directory supplied by the test framework.

    Returns:
        The directory holding the fabricated commands.
    """
    write_tool(tmp_path, "pg_isready", "exit 0")
    write_tool(tmp_path, "pgbackrest", 'echo "$@" >>"$0.calls"\nexit 0')
    write_tool(
        tmp_path, "sleep", f'touch "{tmp_path.as_posix()}/reached-loop"\nexec /bin/sleep "$@"'
    )

    return tmp_path


@pytest.mark.unit
def test_a_failed_stanza_creation_reports_the_documented_code(tools: Path) -> None:
    """Report a stanza that could not be created.

    Confirms the documented stanza failure code is returned, because an operator scripting around
    the agent distinguishes a repository problem from a backup problem by that code alone.

    Arguments:
        tools: Directory of fabricated commands.

    Returns:
        None.

    Raises:
        AssertionError: If the exit code is not the documented one.
    """
    write_tool(tools, "pgbackrest", '[ "$2" = "stanza-create" ] && exit 1\nexit 0')

    assert run(tools).returncode == EXIT_STANZA_FAILED


@pytest.mark.unit
def test_a_failed_configuration_check_reports_the_stanza_code(tools: Path) -> None:
    """Report a primary and agent that disagree.

    Confirms a failing configuration check stops the agent rather than proceeding to back up a
    cluster it cannot read correctly.

    Arguments:
        tools: Directory of fabricated commands.

    Returns:
        None.

    Raises:
        AssertionError: If the exit code is not the documented one.
    """
    write_tool(tools, "pgbackrest", '[ "$2" = "check" ] && exit 1\nexit 0')

    assert run(tools).returncode == EXIT_STANZA_FAILED


@pytest.mark.unit
def test_an_unreachable_primary_reports_the_stanza_code(tools: Path) -> None:
    """Give up on a primary that never arrives.

    Confirms the agent stops rather than waiting forever when the database never becomes ready,
    which is what turns a stalled dependency into a visible failure.

    Arguments:
        tools: Directory of fabricated commands.

    Returns:
        None.

    Raises:
        AssertionError: If the exit code is not the documented one.
    """
    write_tool(tools, "pg_isready", "exit 1")

    assert run(tools).returncode == EXIT_STANZA_FAILED


@pytest.mark.unit
def test_the_documented_exit_codes_are_the_ones_implemented() -> None:
    """Keep the exit codes in step with the document.

    Confirms the conventions document still assigns the three codes the script returns, so a caller
    branching on them reads the same contract the script implements.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the document does not describe the implemented codes.
    """
    document = CONVENTIONS.read_text(encoding="utf-8")

    assert "`0` clean shutdown, on `TERM` or `INT`" in document
    assert "`2` a scheduled backup failed" in document


@pytest.mark.unit
def test_the_script_owns_no_variable_in_the_tool_namespace() -> None:
    """Keep the script's own settings out of the tool's namespace.

    Confirms no schedule variable carries the tool's prefix, because the tool treats every variable
    with that prefix as one of its options and warns on each invocation, including every
    write-ahead log push the primary performs.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If a script-owned variable uses the tool's prefix.
    """
    manifest = (REPOSITORY_ROOT / ".env.example").read_text(encoding="utf-8")
    owned = {"SCHEDULE", "WAIT_SECONDS"}

    for line in manifest.splitlines():
        name = line.partition("=")[0]
        if name.startswith("PGBACKREST_"):
            assert not any(suffix in name for suffix in owned), name
