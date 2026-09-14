"""Unit tests for the application container entrypoint.

Covers the startup order, the handover that keeps companion processes from migrating, and the
signal handling that decides whether the container stops or is killed, using fabricated commands so
no test needs the image.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent.parent.parent
ENTRYPOINT = REPOSITORY_ROOT / "docker" / "django" / "entrypoint.sh"
BASH = shutil.which("bash")

TIMEOUT_SECONDS = 30
MINIMUM_SUPERVISED_PHASES = 3


def write_tool(directory: Path, name: str, body: str) -> None:
    """Place a fabricated command on the search path.

    Writes a small shell script standing in for one of the commands the entrypoint runs, so its
    behaviour can be dictated per test without a Python environment or a database.

    Arguments:
        directory: Directory that will be prepended to the search path.
        name: Command name to fabricate.
        body: Shell body the fabricated command runs.

    Returns:
        None.

    Raises:
        None.
    """
    path = directory / name
    path.write_text(f"#!/usr/bin/env bash\n{body}\n", encoding="utf-8", newline="\n")
    path.chmod(0o755)


def environment(directory: Path) -> dict[str, str]:
    """Build the environment the entrypoint requires.

    Supplies the settings module the script asserts on and puts the fabricated commands first on
    the search path, so nothing real is invoked.

    Arguments:
        directory: Directory holding the fabricated commands.

    Returns:
        The environment for the subprocess.

    Raises:
        None.
    """
    return {
        **os.environ,
        "PATH": f"{directory}{os.pathsep}{os.environ['PATH']}",
        "DJANGO_SETTINGS_MODULE": "config.settings.testing",
        "LOCALFORGE_WAIT_SERVICES": "postgres valkey-cache",
        "LOCALFORGE_WAIT_TIMEOUT": "5",
        "UVICORN_WORKERS": "3",
    }


@pytest.fixture
def tools(tmp_path: Path) -> Path:
    """Provide fabricated commands that record what the entrypoint ran.

    Records every invocation in call order and makes the server exit immediately, so a test reads
    the startup sequence as a list rather than by watching a process.

    Arguments:
        tmp_path: Temporary directory supplied by the test framework.

    Returns:
        The directory holding the fabricated commands.

    Raises:
        None.
    """
    calls = tmp_path / "calls"
    write_tool(tmp_path, "python", f'echo "python $*" >>"{calls.as_posix()}"\nexit 0')
    write_tool(tmp_path, "uvicorn", f'echo "uvicorn $*" >>"{calls.as_posix()}"\nexit 0')

    return tmp_path


def run(directory: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    """Run the entrypoint against the fabricated commands.

    Executes the script as a process so its exit code and ordering are the real ones, which is what
    the container depends on.

    Arguments:
        directory: Directory holding the fabricated commands.
        *arguments: Arguments to hand the entrypoint.

    Returns:
        The completed process.

    Raises:
        subprocess.TimeoutExpired: If the script does not finish in time.
    """
    return subprocess.run(
        [str(BASH), str(ENTRYPOINT), *arguments],
        capture_output=True,
        text=True,
        env=environment(directory),
        cwd=REPOSITORY_ROOT,
        timeout=TIMEOUT_SECONDS,
        check=False,
    )


def calls(directory: Path) -> list[str]:
    """Read the commands the entrypoint ran, in order.

    Reads the file the fabricated commands append to, so a test asserts on the sequence rather than
    on the script's text.

    Arguments:
        directory: Directory holding the fabricated commands.

    Returns:
        Each invocation, in the order it happened.

    Raises:
        None.
    """
    recorded = directory / "calls"

    return recorded.read_text(encoding="utf-8").splitlines() if recorded.exists() else []


@pytest.mark.unit
def test_the_port_is_bound_last(tools: Path) -> None:
    """Open the port only once the instance can serve.

    Confirms the dependency gate, the migration, and the static collection all run before the
    server starts, which is what makes anything waiting on this container healthy also wait on a
    current schema.

    Arguments:
        tools: Directory of fabricated commands.

    Returns:
        None.

    Raises:
        AssertionError: If the startup sequence is not the documented one.
    """
    assert run(tools).returncode == 0

    recorded = calls(tools)

    assert "wait_for_services.py" in recorded[0]
    assert "migrate" in recorded[1]
    assert "collectstatic" in recorded[2]
    assert recorded[3].startswith("uvicorn")


@pytest.mark.unit
def test_an_unready_dependency_stops_the_startup(tools: Path) -> None:
    """Refuse to serve without the dependencies.

    Confirms a failed readiness gate ends the startup rather than continuing, so a container never
    binds its port against a database that is not there.

    Arguments:
        tools: Directory of fabricated commands.

    Returns:
        None.

    Raises:
        AssertionError: If the startup continues past the failed gate.
    """
    write_tool(tools, "python", 'echo "python $*" >>"$0.calls"\nexit 1')

    assert run(tools).returncode != 0
    assert not any("uvicorn" in call for call in calls(tools))


@pytest.mark.unit
def test_a_failed_migration_stops_the_startup(tools: Path) -> None:
    """Refuse to serve on a schema that did not apply.

    Confirms a migration failure ends the startup, because serving against a half-applied schema is
    worse than not serving at all.

    Arguments:
        tools: Directory of fabricated commands.

    Returns:
        None.

    Raises:
        AssertionError: If the server starts after a failed migration.
    """
    calls_file = (tools / "calls").as_posix()
    write_tool(
        tools,
        "python",
        f'echo "python $*" >>"{calls_file}"\ncase "$*" in *migrate*) exit 1 ;; esac\nexit 0',
    )

    assert run(tools).returncode != 0
    assert not any("uvicorn" in call for call in calls(tools))


@pytest.mark.unit
def test_a_companion_command_waits_but_does_not_migrate(tools: Path) -> None:
    """Let a companion process share the image without migrating.

    Confirms a command passed to the entrypoint still waits for dependencies and then replaces the
    script, which is how the worker and the scheduler reuse this image without touching the schema.

    Arguments:
        tools: Directory of fabricated commands.

    Returns:
        None.

    Raises:
        AssertionError: If the companion path migrates or fails to hand over.
    """
    calls_file = (tools / "calls").as_posix()
    write_tool(tools, "celery", f'echo "celery $*" >>"{calls_file}"\nexit 0')

    assert run(tools, "celery", "worker").returncode == 0

    recorded = calls(tools)

    assert "wait_for_services.py" in recorded[0]
    assert recorded[1] == "celery worker"
    assert not any("migrate" in call for call in recorded)


@pytest.mark.unit
def test_the_worker_count_comes_from_the_environment(tools: Path) -> None:
    """Serve with the worker count the environment sets.

    Confirms the configured worker count reaches the server, since the value is the one knob an
    operator has over the container's concurrency.

    Arguments:
        tools: Directory of fabricated commands.

    Returns:
        None.

    Raises:
        AssertionError: If the configured count is not passed through.
    """
    run(tools)

    assert any("--workers 3" in call for call in calls(tools))


@pytest.mark.unit
def test_a_missing_settings_module_stops_the_startup(tools: Path) -> None:
    """Refuse to start without a settings module.

    Confirms the required variable is asserted rather than defaulted, so a container started
    without its environment file fails instead of quietly running another environment.

    Arguments:
        tools: Directory of fabricated commands.

    Returns:
        None.

    Raises:
        AssertionError: If the script starts without the variable.
    """
    incomplete = environment(tools)
    del incomplete["DJANGO_SETTINGS_MODULE"]

    completed = subprocess.run(
        [str(BASH), str(ENTRYPOINT)],
        capture_output=True,
        text=True,
        env=incomplete,
        cwd=REPOSITORY_ROOT,
        timeout=TIMEOUT_SECONDS,
        check=False,
    )

    assert completed.returncode != 0
    assert "settings module is required" in completed.stderr


@pytest.mark.unit
def test_the_script_forwards_stop_signals_to_its_child() -> None:
    """Stop while still starting rather than being killed.

    Confirms the script installs a stop handler and supervises each startup phase, because a shell
    without a handler ignores the signal as process one and the platform then kills the container
    after its grace period, potentially mid-migration.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the handler or the supervision is absent.
    """
    source = ENTRYPOINT.read_text(encoding="utf-8")

    assert "trap forward_signal TERM INT" in source
    assert 'kill -TERM "${child}"' in source
    assert source.count("supervise ") >= MINIMUM_SUPERVISED_PHASES
