"""Shared fixtures for the whole test suite.

Provides the per-worker namespace every externally allocated name is built from, so two workers
running the same test in parallel cannot collide on a bucket, a queue, or a cache key.
"""

import os

import pytest

SERIAL_GROUP = "serial"
_SKIPPED_TESTS: set[str] = set()


def record_skipped_report(
    report: pytest.TestReport | pytest.CollectReport,
    skipped_tests: set[str],
) -> None:
    """Record one skipped runtime or collection report.

    Adds only skipped report node identifiers to the supplied tracker so hook behavior can be
    tested without mutating the live session's evidence.

    Arguments:
        report: Runtime or collection report.
        skipped_tests: Tracker receiving skipped node identifiers.

    Returns:
        None.

    Raises:
        None.
    """
    if report.skipped:
        skipped_tests.add(report.nodeid)


def apply_no_skip_policy(session: pytest.Session, skipped_tests: set[str]) -> None:
    """Apply the no-skip verdict to one controller session.

    Leaves worker sessions unchanged and fails a controller only when its supplied tracker contains
    skipped runtime or collection reports.

    Arguments:
        session: Pytest session finishing execution.
        skipped_tests: Skipped node identifiers observed by the controller.

    Returns:
        None.

    Raises:
        None.
    """
    if hasattr(session.config, "workerinput") or not skipped_tests:
        return
    session.exitstatus = pytest.ExitCode.TESTS_FAILED


def pytest_sessionstart(session: pytest.Session) -> None:
    """Reset skipped-test evidence for one pytest controller.

    Clears retained node identifiers before collection so repeated in-process test runs cannot
    inherit another session's result.

    Arguments:
        session: Pytest session beginning execution.

    Returns:
        None.

    Raises:
        None.
    """
    del session
    _SKIPPED_TESTS.clear()


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    """Record every skipped test report.

    Receives local and xdist-forwarded reports and retains the stable node identifier for the
    controller's final verdict.

    Arguments:
        report: Completed test phase report.

    Returns:
        None.

    Raises:
        None.
    """
    record_skipped_report(report, _SKIPPED_TESTS)


def pytest_collectreport(report: pytest.CollectReport) -> None:
    """Record a module or collector skipped during collection.

    Captures collection-time skip outcomes that never produce a runtime ``TestReport``.
    The controller applies the same final no-skip verdict to both report kinds.

    Arguments:
        report: Completed collection report.

    Returns:
        None.

    Raises:
        None.
    """
    record_skipped_report(report, _SKIPPED_TESTS)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Fail the controller session when any test was skipped.

    Leaves worker-process exit handling unchanged while converting an otherwise successful
    controller result into a test failure whenever a skip reached the report stream.

    Arguments:
        session: Pytest session finishing execution.
        exitstatus: Status produced by pytest before the no-skip policy.

    Returns:
        None.

    Raises:
        None.
    """
    del exitstatus
    apply_no_skip_policy(session, _SKIPPED_TESTS)


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(
    config: pytest.Config,
    items: list[pytest.Item],
) -> None:
    """Pin every serial test to one worker.

    Stamps each test marked serial with a shared distribution group, so the marker actually
    serialises rather than documenting an intention the parallel runner ignores.

    Arguments:
        config: Active pytest configuration.
        items: Tests collected for this run.

    Returns:
        None.

    Raises:
        None.
    """
    try:
        loadgroup = bool(config.getvalue("loadgroup"))
    except ValueError:
        loadgroup = False
    for item in items:
        if item.get_closest_marker(SERIAL_GROUP) is not None:
            item.add_marker(pytest.mark.xdist_group(SERIAL_GROUP))
            if loadgroup:
                base_nodeid = item.nodeid.rsplit("@", maxsplit=1)[0]
                item._nodeid = f"{base_nodeid}@{SERIAL_GROUP}"


@pytest.fixture(scope="session")
def worker_id() -> str:
    """Name the worker this test is running on.

    Reads the identifier the parallel runner exports, falling back to a fixed name when the suite
    runs in one process, so a fixture can namespace a resource without knowing how it was invoked.

    Arguments:
        None.

    Returns:
        The worker identifier, such as ``gw0``, or ``main`` in a serial run.

    Raises:
        None.
    """
    return os.environ.get("PYTEST_XDIST_WORKER", "main")


@pytest.fixture(scope="session")
def worker_namespace(worker_id: str) -> str:
    """Build the prefix every externally allocated name carries.

    Combines the project name with the worker identifier, so a bucket, queue, group, or cache key
    created by one worker cannot be seen, mutated, or deleted by another running at the same time.

    Arguments:
        worker_id: Identifier of the worker this test runs on.

    Returns:
        The prefix to put in front of any externally allocated name.

    Raises:
        None.
    """
    return f"localforge-test-{worker_id}"
