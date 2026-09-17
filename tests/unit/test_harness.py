"""Unit tests for the test harness itself.

Covers the per-worker namespace, the guards that keep unit tests off the network and off the
database, and the guard that makes every integration test name its services, so the suite's own
rules are checked the same way the code under test is.
"""

import asyncio
import socket
import subprocess
import sys
import tomllib
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import psycopg
import pytest

from tests.integration import conftest as integration_conftest
from tests.unit.conftest import NetworkAccessInUnitTestError

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent.parent
TOKEN_AUTHENTICATION_TEST = (
    REPOSITORY_ROOT / "tests" / "integration" / "accounts" / "test_token_authentication.py"
)
JWT_AUTHENTICATION_TEST = (
    REPOSITORY_ROOT / "tests" / "integration" / "accounts" / "test_jwt_authentication.py"
)
REGISTRATION_TIMING_TEST = (
    REPOSITORY_ROOT / "tests" / "integration" / "accounts" / "test_registration_timing.py"
)
PASSWORD_MANAGEMENT_TEST = (
    REPOSITORY_ROOT / "tests" / "integration" / "accounts" / "test_password_management.py"
)
USERNAME_MANAGEMENT_TEST = (
    REPOSITORY_ROOT / "tests" / "integration" / "accounts" / "test_username_management.py"
)
SECURITY_TIMING_TESTS = (
    TOKEN_AUTHENTICATION_TEST,
    JWT_AUTHENTICATION_TEST,
    REGISTRATION_TIMING_TEST,
    PASSWORD_MANAGEMENT_TEST,
    USERNAME_MANAGEMENT_TEST,
)
SECURITY_TIMING_CASES_BY_TEST = {
    TOKEN_AUTHENTICATION_TEST: 14,
    JWT_AUTHENTICATION_TEST: 5,
    REGISTRATION_TIMING_TEST: 2,
    PASSWORD_MANAGEMENT_TEST: 1,
    USERNAME_MANAGEMENT_TEST: 1,
}
SECURITY_TIMING_CASES = 23
UNDECLARED_INTEGRATION_TEST = '''"""Probe module for the collection guard.

Holds one integration test that names no service, so a real collection can be observed rejecting
it rather than the rule being checked only against a fabricated item.
"""

import pytest


@pytest.mark.integration
def test_probe() -> None:
    """Do nothing at all.

    Exists only to be collected, carrying the integration marker and no services declaration, which
    is the combination the guard must refuse.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        None.
    """
'''


def _configured_tasks() -> dict[str, Any]:
    """Read the task-runner interface from the project manifest.

    Parses the public task declarations callers invoke, keeping runner contract assertions at the
    same seam as developers, containers, and automation rather than importing Poe internals.

    Arguments:
        None.

    Returns:
        Task names mapped to their declared configuration.

    Raises:
        KeyError: If the manifest does not declare the Poe task table.
    """
    manifest = tomllib.loads((REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    return cast("dict[str, Any]", manifest["tool"]["poe"]["tasks"])


def _collected_security_timing_cases(
    marker_expression: str | None = None,
    *,
    paths: tuple[Path, ...] = SECURITY_TIMING_TESTS,
) -> set[str]:
    """Collect security-timing cases through pytest's command interface.

    Runs collection without the suite's execution defaults and returns the node identifiers pytest
    exposes, allowing either the declared timing modules or the global suite partition to be
    compared without executing service-backed tests.

    Arguments:
        marker_expression: Optional pytest marker expression selecting one partition.
        paths: Files or directories whose collected cases form the comparison population.

    Returns:
        Collected security-timing node identifiers.

    Raises:
        AssertionError: If pytest cannot collect the requested partition.
    """
    command = [
        sys.executable,
        "-m",
        "pytest",
        "--collect-only",
        "-q",
        "-p",
        "no:cacheprovider",
        "-o",
        "addopts=",
    ]
    if marker_expression is not None:
        command.extend(["-m", marker_expression])
    command.extend(str(path) for path in paths)
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
        cwd=REPOSITORY_ROOT,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr

    return {
        line.strip()
        for line in completed.stdout.splitlines()
        if line.startswith("tests/") and "::" in line
    }


def _item(nodeid: str, markers: dict[str, object]) -> Any:  # noqa: ANN401
    """Build a stand-in for one collected test.

    Provides only the marker lookup the collection hook uses, so each rule can be exercised in
    isolation; the end-to-end test below proves the hook behaves the same on real collected items.

    Arguments:
        nodeid: Identifier the guard reports the test by.
        markers: Marker name mapped to the marker object, or absent when the test lacks it.

    Returns:
        An object the collection hook can treat as a collected test.

    Raises:
        None.
    """
    return SimpleNamespace(nodeid=nodeid, get_closest_marker=markers.get)


def _marker(*names: object) -> object:
    """Build a stand-in for a services marker.

    Carries only the arguments the guard reads, so a declaration can be fabricated without applying
    a real marker to a real test.

    Arguments:
        *names: Service names the fabricated marker declares.

    Returns:
        An object the guard can read arguments from.

    Raises:
        None.
    """
    return SimpleNamespace(args=names)


@pytest.mark.unit
def test_the_worker_namespace_carries_the_worker_identity(worker_namespace: str) -> None:
    """Namespace every externally allocated name per worker.

    Confirms the namespace carries the identity of the worker asking for it, which is what stops
    two workers creating the same bucket or queue and deleting each other's state.

    Arguments:
        worker_namespace: Namespace fixture under test.

    Returns:
        None.

    Raises:
        AssertionError: If the namespace does not carry a worker identity.
    """
    assert worker_namespace.startswith("localforge-test-")
    assert worker_namespace != "localforge-test-"


@pytest.mark.unit
def test_a_unit_test_cannot_open_a_connection() -> None:
    """Keep the unit layer off the network.

    Confirms an outbound connection from a unit test fails immediately and names the address,
    because a unit test that reaches a service passes on one machine and hangs on another.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the connection is not refused.
    """
    with (
        socket.socket() as probe,
        pytest.raises(NetworkAccessInUnitTestError, match="integration layer"),
    ):
        probe.connect(("127.0.0.1", 25432))


@pytest.mark.unit
def test_a_unit_test_cannot_reach_the_database() -> None:
    """Keep the unit layer off the database.

    Confirms the driver's own connect is refused, because its C client opens a socket the Python
    guard never sees and would otherwise reach a running server unnoticed.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the connection is not refused.
    """
    with pytest.raises(NetworkAccessInUnitTestError, match="integration layer"):
        psycopg.connect(host="127.0.0.1", port=25432, dbname="localforge", connect_timeout=1)


@pytest.mark.unit
def test_an_event_loop_still_starts_under_the_guard() -> None:
    """Leave asynchronous tests working.

    Confirms an event loop can be created and run while the guard is active, because the loop wakes
    itself through a loopback socket pair on this platform and refusing that would fail every
    consumer test for a reason unrelated to the code under test.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the loop cannot complete a trivial coroutine.
    """

    async def probe() -> str:
        """Return a value from inside the loop.

        Exists so the test has a coroutine to run, which is what forces the loop to build its
        wake-up socket pair.

        Arguments:
            None.

        Returns:
            A fixed value.

        Raises:
            None.
        """
        await asyncio.sleep(0)

        return "ran"

    assert asyncio.run(probe()) == "ran"


@pytest.mark.unit
def test_an_integration_test_must_declare_its_services() -> None:
    """Refuse an integration test with no declared services.

    Confirms collection fails and names the offending test, since an undeclared dependency turns a
    missing service into an unattributable connection error.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If collection does not fail naming the test.
    """
    items = [_item("tests/integration/config/test_probe.py::test_probe", {"integration": object()})]

    with pytest.raises(pytest.UsageError, match="test_probe"):
        integration_conftest.pytest_collection_modifyitems(items)


@pytest.mark.unit
def test_a_declared_integration_test_is_collected() -> None:
    """Accept an integration test that declares its services.

    Confirms a declared test passes the guard untouched, so the rule costs nothing once a test says
    what it needs.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If a declared test is rejected.
    """
    items = [
        _item(
            "tests/integration/config/test_probe.py::test_probe",
            {"integration": object(), "services": _marker("postgres")},
        )
    ]

    integration_conftest.pytest_collection_modifyitems(items)

    assert items


@pytest.mark.unit
@pytest.mark.parametrize("names", [(), ("",), ("  ",), (1,)])
def test_an_empty_or_unusable_services_declaration_is_refused(names: tuple[object, ...]) -> None:
    """Refuse a declaration that names nothing usable.

    Confirms an empty or malformed marker does not satisfy the rule, since a declaration carrying
    no service name documents no more than omitting the marker entirely.

    Arguments:
        names: Arguments the fabricated marker carries.

    Returns:
        None.

    Raises:
        AssertionError: If the unusable declaration is accepted.
    """
    items = [
        _item(
            "tests/integration/config/test_probe.py::test_probe",
            {"integration": object(), "services": _marker(*names)},
        )
    ]

    with pytest.raises(pytest.UsageError, match="test_probe"):
        integration_conftest.pytest_collection_modifyitems(items)


@pytest.mark.unit
def test_a_unit_test_needs_no_services_declaration() -> None:
    """Leave the unit layer out of the services rule.

    Confirms a test outside the integration layer is not asked to declare services, because it is
    forbidden from using any.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If a unit test is rejected.
    """
    items = [_item("tests/unit/test_probe.py::test_probe", {"unit": object()})]

    integration_conftest.pytest_collection_modifyitems(items)

    assert items


@pytest.mark.unit
def test_the_services_guard_fires_during_real_collection(tmp_path: Path) -> None:
    """Reject an undeclared integration test during a real collection.

    Runs a genuine collection over a fabricated integration test, so the guard is proven against
    the items pytest builds rather than only against the stand-ins the tests above supply.

    Arguments:
        tmp_path: Temporary directory supplied by the test framework.

    Returns:
        None.

    Raises:
        AssertionError: If collection does not fail naming the undeclared test.
    """
    tree = tmp_path / "integration"
    tree.mkdir()
    (tree / "conftest.py").write_text(
        Path(str(integration_conftest.__file__)).read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (tree / "test_probe.py").write_text(UNDECLARED_INTEGRATION_TEST, encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "-p",
            "no:cacheprovider",
            "-p",
            "no:django",
            "-o",
            "addopts=",
            "-o",
            "markers=integration: tests spanning Django components\nservices(*names): services",
            str(tree),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )

    assert completed.returncode != 0
    assert "must name the services it needs" in completed.stdout + completed.stderr


@pytest.mark.unit
def test_complete_test_tasks_compose_core_and_security_timing_stages() -> None:
    """Keep every complete task behind the same two-stage runner interface.

    Confirms the default, fresh-database, and compatibility task names all execute the core stage
    before the bounded security timing stage, so no complete gate can silently omit either.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If a complete task does not compose both stages.
    """
    tasks = _configured_tasks()

    assert tasks["test"]["sequence"] == ["test-core", "test-security-timing"]
    assert tasks["test-fresh"]["sequence"] == ["test-core-fresh", "test-security-timing"]
    assert tasks["test-parallel"]["sequence"] == ["test-core", "test-security-timing"]


@pytest.mark.unit
def test_core_and_security_timing_tasks_preserve_their_distinct_invariants() -> None:
    """Keep coverage and wall-clock measurement concerns in separate stages.

    Confirms core execution retains automatic load-group distribution and coverage while timing
    execution selects only its explicit marker with four independent workers and a distinct report.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If either stage weakens or overlaps the other.
    """
    tasks = _configured_tasks()
    core_command = tasks["test-core"]["cmd"]
    timing_command = tasks["test-security-timing"]["cmd"]

    assert "-n auto --dist loadgroup" in core_command
    assert '-m "not security_timing"' in core_command
    assert "--max-worker-restart=0" in core_command
    assert "--junitxml=test-results/pytest-core.xml" in core_command
    assert "--no-cov" not in core_command
    assert "-n 4 --dist load" in timing_command
    assert "-m security_timing" in timing_command
    assert "--no-cov" in timing_command
    assert "--max-worker-restart=0" in timing_command
    assert "--junitxml=test-results/pytest-security-timing.xml" in timing_command
    assert "--log-level=INFO" in timing_command
    assert "-o junit_logging=all" in timing_command


@pytest.mark.unit
def test_focused_integration_task_excludes_security_timing_by_default() -> None:
    """Keep focused integration feedback free of statistical benchmarks.

    Confirms the integration task omits wall-clock cases unless callers choose the dedicated timing
    interface, preventing an ordinary focused run from unexpectedly inheriting benchmark cost.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If focused integration execution includes timing cases.
    """
    command = _configured_tasks()["test-integration"]["cmd"]

    assert '-m "integration and not security_timing"' in command


@pytest.mark.unit
def test_security_timing_marker_partitions_every_statistical_case_exactly_once() -> None:
    """Partition global statistical timing cases from deterministic core coverage.

    Collects the whole suite through pytest and proves the timing and core selections are disjoint,
    exhaustive, and contain exactly the approved twenty-three cases in their declared modules.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If a case is missing, duplicated, or assigned to the wrong stage.
    """
    global_paths = (REPOSITORY_ROOT / "tests",)
    all_cases = _collected_security_timing_cases(paths=global_paths)
    timing_cases = _collected_security_timing_cases("security_timing", paths=global_paths)
    core_cases = _collected_security_timing_cases("not security_timing", paths=global_paths)
    cases_by_test = Counter(
        REPOSITORY_ROOT / nodeid.split("::", maxsplit=1)[0] for nodeid in timing_cases
    )

    assert len(timing_cases) == SECURITY_TIMING_CASES
    assert cases_by_test == Counter(SECURITY_TIMING_CASES_BY_TEST)
    assert timing_cases.isdisjoint(core_cases)
    assert timing_cases | core_cases == all_cases
