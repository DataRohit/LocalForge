"""Integration tests for database routing across both nodes.

Covers which connection actually carried each statement, rather than which alias the router names,
because only an executed query proves the routing the application depends on.
"""

import pytest
from django.db import connections, transaction
from django.test.utils import CaptureQueriesContext

from accounts.models import User
from config.db_router import PRIMARY, REPLICA

PASSWORD = "probe-routing-passphrase"  # noqa: S105


def _carried(capture: CaptureQueriesContext, fragment: str) -> bool:
    """Report whether a connection carried a statement of one kind.

    Scans the statements captured on one connection for the fragment under test, so an assertion
    names the connection a statement travelled over rather than the alias the router would pick.

    Arguments:
        capture: Capture taken around the work under test.
        fragment: Statement fragment to look for.

    Returns:
        True when that connection carried such a statement.

    Raises:
        None.
    """
    return any(fragment in query["sql"] for query in capture.captured_queries)


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_a_write_travels_over_the_primary_connection() -> None:
    """Send a write over the primary connection.

    Confirms the insert was carried by the primary connection and not by the standby, which is
    read-only and would reject the statement rather than forward it.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the write did not travel over the primary alone.
    """
    with (
        CaptureQueriesContext(connections[PRIMARY]) as primary,
        CaptureQueriesContext(connections[REPLICA]) as replica,
    ):
        User.objects.create_user("routing-write", "routing-write@localforge.invalid", PASSWORD)

    assert _carried(primary, "INSERT INTO")
    assert not _carried(replica, "INSERT INTO")


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_a_read_travels_over_the_replica_connection() -> None:
    """Serve a read over the replica connection.

    Confirms an ordinary read was carried by the replica connection, which is the arrangement that
    keeps a read-heavy request off the primary's capacity.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the read did not travel over the replica alone.
    """
    User.objects.create_user("routing-read", "routing-read@localforge.invalid", PASSWORD)

    with (
        CaptureQueriesContext(connections[PRIMARY]) as primary,
        CaptureQueriesContext(connections[REPLICA]) as replica,
    ):
        assert User.objects.filter(username="routing-read").exists()

    assert _carried(replica, "SELECT")
    assert not _carried(primary, "SELECT")


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_a_read_inside_an_atomic_block_sees_its_own_write() -> None:
    """Read back what the block has just written.

    Confirms a read inside an atomic block travels over the primary and finds the uncommitted row,
    because the replica cannot see another connection's open transaction and a block unable to read
    its own write would be a silent correctness bug.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the read misses the row or travels over the replica.
    """
    with transaction.atomic():
        User.objects.create_user("routing-atomic", "routing-atomic@localforge.invalid", PASSWORD)

        with (
            CaptureQueriesContext(connections[PRIMARY]) as primary,
            CaptureQueriesContext(connections[REPLICA]) as replica,
        ):
            found = User.objects.filter(username="routing-atomic").exists()

        assert found is True
        assert _carried(primary, "SELECT")
        assert not _carried(replica, "SELECT")
