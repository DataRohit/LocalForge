"""Unit tests for the primary and replica database router.

Covers which alias each kind of statement is sent to and which alias may be migrated, since the
migration rule is the project's own and a wrong answer there would point schema changes at a
read-only server.
"""

import os
from pathlib import Path
from unittest import mock

import pytest
from django.conf import settings
from django.db import connections

from accounts.models import User
from config.db_router import PRIMARY, REPLICA, PrimaryReplicaRouter


@pytest.mark.unit
def test_a_read_is_served_from_the_replica() -> None:
    """Send reads to the standby.

    Confirms an ordinary read is routed to the replica alias, which is the whole reason the standby
    exists and what keeps a read-heavy request off the primary's capacity.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If a read is routed elsewhere.
    """
    with mock.patch.object(connections[PRIMARY], "in_atomic_block", new=False):
        assert PrimaryReplicaRouter().db_for_read(User) == REPLICA


@pytest.mark.unit
def test_a_read_inside_an_atomic_block_is_served_from_the_primary() -> None:
    """Keep a transaction reading its own writes.

    Confirms a read is routed to the primary while an atomic block is open, because Django does not
    reroute for transactions itself and the replica cannot see another connection's open one.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the read is routed to the replica inside a block.
    """
    with mock.patch.object(connections[PRIMARY], "in_atomic_block", new=True):
        assert PrimaryReplicaRouter().db_for_read(User) == PRIMARY


@pytest.mark.unit
def test_a_write_is_sent_to_the_primary() -> None:
    """Send writes to the primary.

    Confirms a write is routed to the primary alias, because the standby is physically read-only
    and would reject the statement rather than forward it.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If a write is routed elsewhere.
    """
    assert PrimaryReplicaRouter().db_for_write(User) == PRIMARY


@pytest.mark.unit
def test_only_the_primary_may_be_migrated() -> None:
    """Keep schema changes off the standby.

    Confirms migration is permitted on the primary and refused on the replica, which is this
    project's own rule: the framework's own example answers true for every alias.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the replica is offered for migration.
    """
    router = PrimaryReplicaRouter()

    assert router.allow_migrate(PRIMARY, "accounts") is True
    assert router.allow_migrate(REPLICA, "accounts") is False


@pytest.mark.unit
def test_objects_on_either_alias_may_be_related() -> None:
    """Relate objects across the two aliases.

    Confirms relations are permitted, because both aliases address one physical cluster rather than
    two databases with separate contents.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If a relation is refused.
    """
    assert PrimaryReplicaRouter().allow_relation(User(), User()) is True


@pytest.mark.unit
def test_the_router_answers_last() -> None:
    """Keep the catch-all at the end of the list.

    Confirms this router is the final entry, since Django takes the first non-null answer and this
    one answers for every model: anything placed after it could never be consulted.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If another router is configured after it.
    """
    assert settings.DATABASE_ROUTERS[-1] == "config.db_router.PrimaryReplicaRouter"


@pytest.mark.unit
def test_both_aliases_come_from_the_environment() -> None:
    """Address both nodes from the inventory.

    Confirms each alias takes its host and port from the environment and that the replica is a
    separate endpoint, so pointing either at another node is a configuration change.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If an alias is missing or carries a literal address.
    """
    aliases = settings.DATABASES

    assert set(aliases) == {PRIMARY, REPLICA}
    assert aliases[REPLICA]["HOST"] == os.environ["POSTGRES_REPLICA_HOST"]
    assert aliases[REPLICA]["PORT"] == int(os.environ["POSTGRES_REPLICA_PORT"])
    assert aliases[PRIMARY]["HOST"] == os.environ["POSTGRES_HOST"]


@pytest.mark.unit
def test_connections_are_pooled_checked_and_bounded() -> None:
    """Replace a connection rather than reuse a dead one.

    Confirms both aliases enable health checking, pool their connections with a bounded lifetime,
    and size the pool from the worker count rather than leaving it at the default.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If pooling, checking, or the lifetime bound is absent.
    """
    workers = int(os.environ.get("UVICORN_WORKERS", "2"))

    for alias in (PRIMARY, REPLICA):
        configuration = settings.DATABASES[alias]
        pool = configuration["OPTIONS"]["pool"]

        assert configuration["CONN_HEALTH_CHECKS"] is True
        assert configuration["CONN_MAX_AGE"] == 0
        assert pool["max_lifetime"] == settings.DATABASE_CONNECTION_MAX_LIFETIME_SECONDS
        assert pool["max_size"] == max(
            settings.DATABASE_POOL_MINIMUM_SIZE,
            settings.DATABASE_WEB_CONNECTION_BUDGET // (workers * 2),
        )


@pytest.mark.unit
def test_the_replica_mirrors_the_primary_under_test() -> None:
    """Exercise the router without a second test database.

    Confirms the replica is declared a mirror, so the suite builds one test database while every
    routing path still runs, which is the arrangement the replication decision record describes.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the replica is not mirrored onto the primary.
    """
    assert settings.DATABASES[REPLICA]["TEST"]["MIRROR"] == PRIMARY


@pytest.mark.unit
def test_replication_lag_is_a_recorded_limitation() -> None:
    """State what the suite does not cover.

    Confirms the replication decision record still says lag is out of scope, because a read that
    arrives before its write has replicated cannot be reproduced against a single node and would
    otherwise look like an untested accident rather than an accepted limit.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the limitation is no longer recorded.
    """
    record = " ".join(
        (Path(settings.BASE_DIR).parent / "docs" / "adr" / "0012-streaming-replication.md")
        .read_text(encoding="utf-8")
        .split()
    )

    assert "Replication lag behaviour is therefore not covered by the suite" in record
