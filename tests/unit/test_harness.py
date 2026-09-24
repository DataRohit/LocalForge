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
from typing import TYPE_CHECKING, Any, cast

import psycopg
import pytest

from tests import conftest as suite_conftest
from tests.factories import (
    build_activation_token,
    build_login_throttle_event,
    build_password_reset_token,
    build_user,
    build_username_reset_token,
)
from tests.integration import conftest as integration_conftest
from tests.unit.conftest import NetworkAccessInUnitTestError, posix_shell_candidates
from tests.websocket import WebsocketCommunicator

if TYPE_CHECKING:
    from asgiref.typing import ASGI3Application, ASGIReceiveCallable, ASGISendCallable, Scope

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent.parent
SOURCE_ROOT = REPOSITORY_ROOT / "src"
UNIT_ROOT = REPOSITORY_ROOT / "tests" / "unit"
DIGEST_HEX_LENGTH = 64
BEHAVIORAL_PACKAGE_TESTS = {
    Path("config/__init__.py"): UNIT_ROOT / "config" / "test_package.py",
    Path("config/settings/__init__.py"): UNIT_ROOT / "config" / "settings" / "test_package.py",
}
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
API_RUNTIME_TESTS = (
    REPOSITORY_ROOT / "tests" / "integration" / "accounts" / "test_account_activation.py",
    REPOSITORY_ROOT / "tests" / "integration" / "accounts" / "test_jwt_authentication.py",
    REPOSITORY_ROOT / "tests" / "integration" / "accounts" / "test_password_management.py",
    REPOSITORY_ROOT / "tests" / "integration" / "accounts" / "test_registration_timing.py",
    REPOSITORY_ROOT / "tests" / "integration" / "accounts" / "test_token_authentication.py",
    REPOSITORY_ROOT / "tests" / "integration" / "accounts" / "test_user_profiles.py",
    REPOSITORY_ROOT / "tests" / "integration" / "accounts" / "test_username_management.py",
    REPOSITORY_ROOT / "tests" / "integration" / "config" / "test_api.py",
    REPOSITORY_ROOT / "tests" / "integration" / "config" / "test_security.py",
)


@pytest.mark.unit
def test_skipped_tests_fail_only_the_controller_session() -> None:
    """Convert skipped reports into a failed complete test session.

    Exercises clean, skipped, and xdist-worker finalization so skips are mechanically forbidden
    without changing worker-process exit handling.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If skips remain successful or workers rewrite their status.
    """
    skipped_tests: set[str] = set()
    runtime_report = cast(
        "pytest.TestReport",
        cast(
            "object",
            SimpleNamespace(skipped=True, nodeid="tests/test_probe.py::test_skipped"),
        ),
    )
    collection_report = cast(
        "pytest.CollectReport",
        cast("object", SimpleNamespace(skipped=True, nodeid="tests/test_module.py")),
    )
    passing_report = cast(
        "pytest.TestReport",
        cast(
            "object",
            SimpleNamespace(skipped=False, nodeid="tests/test_probe.py::test_passed"),
        ),
    )
    suite_conftest.record_skipped_report(passing_report, skipped_tests)
    suite_conftest.record_skipped_report(runtime_report, skipped_tests)
    suite_conftest.record_skipped_report(collection_report, skipped_tests)
    assert skipped_tests == {
        "tests/test_module.py",
        "tests/test_probe.py::test_skipped",
    }

    controller = SimpleNamespace(config=SimpleNamespace(), exitstatus=pytest.ExitCode.OK)
    controller_session = cast("pytest.Session", cast("object", controller))
    suite_conftest.apply_no_skip_policy(controller_session, skipped_tests)
    assert controller.exitstatus == pytest.ExitCode.TESTS_FAILED

    worker = SimpleNamespace(
        config=SimpleNamespace(workerinput={}),
        exitstatus=pytest.ExitCode.OK,
    )
    worker_session = cast("pytest.Session", cast("object", worker))
    suite_conftest.apply_no_skip_policy(worker_session, skipped_tests)
    assert worker.exitstatus == pytest.ExitCode.OK

    clean_controller = SimpleNamespace(
        config=SimpleNamespace(),
        exitstatus=pytest.ExitCode.OK,
    )
    suite_conftest.apply_no_skip_policy(
        cast("pytest.Session", cast("object", clean_controller)),
        set(),
    )
    assert clean_controller.exitstatus == pytest.ExitCode.OK


@pytest.mark.unit
def test_serial_tests_override_inherited_xdist_groups() -> None:
    """Force every serial test onto one final load-group node identifier.

    Models a test that already inherited a module group and requires collection normalization to
    replace the combined suffix with the single shared serial group.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If inherited grouping survives or the serial marker is omitted.
    """
    markers: list[object] = []
    item = SimpleNamespace(
        _nodeid=(
            "tests/integration/accounts/test_username_management.py"
            "::test_username_recovery@username-management"
        ),
        nodeid=(
            "tests/integration/accounts/test_username_management.py"
            "::test_username_recovery@username-management"
        ),
        get_closest_marker=lambda name: object() if name == "serial" else None,
        add_marker=markers.append,
    )
    config = SimpleNamespace(getvalue=lambda name: name == "loadgroup")

    suite_conftest.pytest_collection_modifyitems(
        cast("pytest.Config", cast("object", config)),
        [cast("pytest.Item", cast("object", item))],
    )

    normalized = cast("str", vars(item)["_nodeid"])
    assert normalized.endswith("@serial")
    assert "username-management" not in normalized
    assert len(markers) == 1


@pytest.mark.unit
def test_windows_posix_shell_candidates_prefer_git_bash() -> None:
    """Exclude the WSL launcher and prefer Git Bash on Windows.

    Supplies the original failing System32 path together with Git for Windows and requires every
    Git-derived candidate to precede any safe ambient fallback.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the WSL launcher survives or Git Bash loses priority.
    """
    candidates = posix_shell_candidates(
        r"C:\Windows\System32\bash.exe",
        r"C:\Program Files\Git\cmd\git.exe",
        windows=True,
    )

    assert candidates
    assert candidates[0] == Path(r"C:\Program Files\Git\cmd\bash")
    assert all(
        "windows/system32/bash" not in candidate.as_posix().casefold() for candidate in candidates
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_websocket_communicator_wait_reports_timeout() -> None:
    """Report a hung ASGI application as a timeout.

    Starts an application that never completes and requires the communicator to cancel its task
    only after the timeout is translated into the documented exception.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If timeout is swallowed or the application task remains active.
    """

    async def application(
        _scope: Scope,
        _receive: ASGIReceiveCallable,
        _send: ASGISendCallable,
    ) -> None:
        """Wait forever without emitting a WebSocket event.

        Provides a deterministic hung application whose cancellation belongs to communicator
        timeout cleanup.

        Arguments:
            _scope: Unused WebSocket scope.
            _receive: Unused inbound event callable.
            _send: Unused outbound event callable.

        Returns:
            Never returns.

        Raises:
            asyncio.CancelledError: When communicator timeout cleanup cancels the task.
        """
        await asyncio.Event().wait()

    communicator = WebsocketCommunicator(
        cast("ASGI3Application", application),
        "/ws/notifications/",
    )

    with pytest.raises(TimeoutError):
        await communicator.wait(0.01)

    assert communicator.future.cancelled()


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
MISSING_API_CACHE_TEST = '''"""Probe module for the API dependency guard.

Holds one explicitly declared runtime API test without its cache service, so real pytest
collection must reject the incomplete dependency contract.
"""

import pytest


@pytest.mark.integration
@pytest.mark.api_runtime
@pytest.mark.services("postgres")
def test_probe() -> None:
    """Do nothing at all.

    Exists only to be collected with an incomplete runtime API dependency declaration.

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
    path_text, _, test_name = nodeid.partition("::")

    return SimpleNamespace(
        nodeid=nodeid,
        path=REPOSITORY_ROOT / path_text,
        originalname=test_name or None,
        get_closest_marker=markers.get,
    )


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
def test_every_source_module_has_one_mirrored_unit_module() -> None:
    """Keep unit coverage discoverable from each source path.

    Maps every hand-written source module to the exact unit-test path that owns its isolated
    behavior, excluding only package markers and generated migrations.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If any source module lacks its exact mirrored unit module.
    """
    source_modules = {
        path.relative_to(SOURCE_ROOT)
        for path in SOURCE_ROOT.rglob("*.py")
        if path.name != "__init__.py" and "migrations" not in path.parts
    }
    expected_tests = {
        UNIT_ROOT / source.parent / f"test_{source.name}" for source in source_modules
    } | set(BEHAVIORAL_PACKAGE_TESTS.values())
    missing = sorted(
        path.relative_to(REPOSITORY_ROOT).as_posix()
        for path in expected_tests
        if not path.is_file()
    )

    assert missing == []


@pytest.mark.unit
def test_shared_factories_build_valid_objects_without_services() -> None:
    """Build default account and credential-state objects in isolation.

    Verifies every shared factory supplies the required identifiers and safe digest-shaped state
    without persisting or opening a service connection.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If a default factory object is incomplete or credential-bearing.
    """
    account = build_user()
    login_event = build_login_throttle_event()
    activation = build_activation_token()
    password_reset = build_password_reset_token()
    username_reset = build_username_reset_token()

    account.clean_fields()

    assert account.username
    assert account.email.endswith("@localforge.invalid")
    assert account.has_usable_password()
    assert login_event.bucket
    assert login_event.request_id
    assert login_event.occurred_at is not None
    for token in (activation, password_reset, username_reset):
        assert token.subject_id is not None
        assert len(token.digest) == DIGEST_HEX_LENGTH


@pytest.mark.unit
def test_user_factory_preserves_explicit_falsey_overrides() -> None:
    """Build the exact invalid values a negative unit test requests.

    Supplies empty identifiers and password and verifies the factory does not replace them with
    valid defaults.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If falsey overrides are discarded.
    """
    account = build_user(username="", email="", password="")

    assert account.username == ""
    assert account.email == ""
    assert account.check_password("")


@pytest.mark.unit
def test_state_factories_preserve_explicit_falsey_overrides() -> None:
    """Build exact empty identifiers and digests for negative tests.

    Supplies falsey overrides to every non-user factory and verifies none are replaced by generated
    defaults.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If a falsey state override is discarded.
    """
    login_event = build_login_throttle_event(request_id="")
    activation = build_activation_token(digest="")
    password_reset = build_password_reset_token(digest="")
    username_reset = build_username_reset_token(digest="")

    assert login_event.request_id == ""
    assert activation.digest == ""
    assert password_reset.digest == ""
    assert username_reset.digest == ""


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
@pytest.mark.parametrize(
    "dispatch_shape",
    [
        "django-client",
        "getattr-alias",
        "nested-executor-callback",
        "direct-asgi-helper",
    ],
    ids=str,
)
def test_an_explicit_api_runtime_contract_must_declare_the_cache_service(
    dispatch_shape: str,
) -> None:
    """Require cache declaration for every explicitly declared API dispatch shape.

    Represents direct clients, aliases, nested callbacks, and ASGI helpers with the same marker,
    proving dependency enforcement is independent of increasingly brittle source inference.

    Arguments:
        dispatch_shape: Human-readable dispatch shape represented by the fabricated item.

    Returns:
        None.

    Raises:
        AssertionError: If the missing cache dependency is not reported.
    """
    item = _item(
        f"tests/integration/config/test_probe.py::test_{dispatch_shape.replace('-', '_')}",
        {
            "integration": object(),
            "api_runtime": object(),
            "services": _marker("postgres"),
        },
    )

    with pytest.raises(pytest.UsageError, match="valkey-cache"):
        integration_conftest.pytest_collection_modifyitems([item])


@pytest.mark.unit
def test_an_explicit_api_runtime_contract_with_cache_is_collected() -> None:
    """Accept any API dispatch implementation once its boundary dependency is explicit.

    Uses no source file or callable inspection, proving aliases, callbacks, and direct ASGI helpers
    are covered by the declared contract rather than by syntax shapes.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If a complete explicit declaration is rejected.
    """
    items = [
        _item(
            "tests/integration/config/test_probe.py::test_nested_callback",
            {
                "integration": object(),
                "api_runtime": object(),
                "services": _marker("postgres", "valkey-cache"),
            },
        )
    ]

    integration_conftest.pytest_collection_modifyitems(items)

    assert items


@pytest.mark.unit
def test_an_explicit_non_api_case_can_opt_out_of_a_runtime_module() -> None:
    """Allow schema, source, and non-API cases to avoid the cache dependency.

    Applies the narrow exemption used inside runtime-marked modules and proves a PostgreSQL-only
    schema case remains collectable without weakening the default API boundary contract.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If an explicitly exempt non-runtime case is rejected.
    """
    items = [
        _item(
            "tests/integration/config/test_probe.py::test_schema_contract",
            {
                "integration": object(),
                "api_runtime": object(),
                "api_runtime_exempt": object(),
                "services": _marker("postgres"),
            },
        )
    ]

    integration_conftest.pytest_collection_modifyitems(items)

    assert items


@pytest.mark.unit
def test_every_known_runtime_api_module_satisfies_the_explicit_contract() -> None:
    """Collect every current API module under the real dependency guard.

    Covers all known Django-client, alias, helper, callback, spawned-worker, and direct-ASGI cases,
    so a new or existing missing cache declaration fails this harness test during collection.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If collection rejects a known runtime API case or finds none.
    """
    collected = _collected_security_timing_cases(paths=API_RUNTIME_TESTS)

    assert collected


@pytest.mark.unit
def test_non_api_marker_alone_does_not_require_the_cache_service() -> None:
    """Avoid assigning the cache dependency outside the fixed API runtime marker.

    Builds an ordinary health-boundary declaration and verifies no path or callable source is
    inspected, preventing unrelated ``/api/v1/`` strings such as Mailpit endpoints from matching.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If an integration case without the API marker is rejected.
    """
    items = [
        _item(
            "tests/integration/config/test_probe.py::test_health",
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
def test_the_api_cache_guard_fires_during_real_collection(tmp_path: Path) -> None:
    """Reject a runtime API test missing Valkey during real pytest collection.

    Runs genuine collection over a fabricated marked test, proving the explicit boundary contract
    is enforced by pytest itself rather than only through direct hook calls.

    Arguments:
        tmp_path: Temporary directory supplied by the test framework.

    Returns:
        None.

    Raises:
        AssertionError: If collection accepts the incomplete API dependency declaration.
    """
    tree = tmp_path / "integration"
    tree.mkdir()
    (tree / "conftest.py").write_text(
        Path(str(integration_conftest.__file__)).read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (tree / "test_probe.py").write_text(MISSING_API_CACHE_TEST, encoding="utf-8")

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
            (
                "markers=integration: tests spanning Django components\n"
                "services(*names): services\n"
                "api_runtime: fixed application API boundary"
            ),
            str(tree),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )

    output = completed.stdout + completed.stderr

    assert completed.returncode != 0
    assert "api_runtime" in output
    assert "valkey-cache" in output


@pytest.mark.unit
def test_complete_test_tasks_compose_core_and_security_timing_stages() -> None:
    """Keep every complete task behind the SMTP-aware runner interface.

    Confirms every public task that can select SMTP tests enters the platform orchestrator while
    its internal stage tasks retain their original focused or staged behavior.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If a complete task does not compose both stages.
    """
    tasks = _configured_tasks()

    assert tasks["test"]["cmd"] == "python -m scripts.manage_platform testing-test-host"
    assert tasks["test-stages"]["sequence"] == ["test-core", "test-security-timing"]
    assert tasks["test-serial"]["cmd"].endswith("testing-test-host-serial")
    assert tasks["test-serial-stages"]["cmd"] == "pytest"
    assert tasks["test-fresh"]["cmd"].endswith("testing-test-host-fresh")
    assert tasks["test-fresh-stages"]["sequence"] == [
        "test-core-fresh",
        "test-security-timing",
    ]
    assert tasks["test-integration"]["cmd"].endswith("testing-test-host-integration")
    assert "integration and not security_timing" in tasks["test-integration-stages"]["cmd"]
    assert tasks["test-parallel"]["sequence"] == ["test"]


@pytest.mark.unit
def test_architecture_audit_is_a_quality_gate() -> None:
    """Keep objective import rules in the complete local gate.

    Requires one focused task for architecture diagnostics and includes it before the complete test
    workflow, so a dependency-direction regression fails without waiting for service-backed tests.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If architecture enforcement is absent or outside the quality gate.
    """
    tasks = _configured_tasks()

    assert tasks["architecture-audit"]["cmd"] == (
        "pytest tests/unit/test_architecture.py --no-cov -q"
    )
    assert "architecture-audit" in tasks["check"]["sequence"]


@pytest.mark.unit
def test_django_gate_runs_a_database_aware_system_check() -> None:
    """Catch database-specific model failures before the runtime entrypoint.

    Requires the host-compatible testing settings check to include the primary database while the
    development settings retain their container-hostname-independent configuration validation.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the quality gate omits database-aware model checks.
    """
    sequence = _configured_tasks()["django-check"]["sequence"]
    commands = [item["cmd"] for item in sequence]

    assert commands == [
        "python src/manage.py check",
        "python src/manage.py check --database default",
        "python src/manage.py makemigrations --check --dry-run",
    ]


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
    tasks = _configured_tasks()
    command = tasks["test-integration-stages"]["cmd"]

    assert tasks["test-integration"]["cmd"].endswith("testing-test-host-integration")
    assert '-m "integration and not security_timing"' in command


@pytest.mark.unit
def test_security_timing_marker_partitions_every_statistical_case_exactly_once() -> None:
    """Register every statistical timing case exactly once.

    Collects the global timing selection once and proves it contains exactly the approved
    twenty-three cases in their declared modules. The dual-mode runner separately enforces complete
    collection equals core plus timing before execution.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If a case is missing, duplicated, or assigned to the wrong stage.
    """
    global_paths = (REPOSITORY_ROOT / "tests",)
    timing_cases = _collected_security_timing_cases("security_timing", paths=global_paths)
    cases_by_test = Counter(
        REPOSITORY_ROOT / nodeid.split("::", maxsplit=1)[0] for nodeid in timing_cases
    )

    assert len(timing_cases) == SECURITY_TIMING_CASES
    assert cases_by_test == Counter(SECURITY_TIMING_CASES_BY_TEST)
