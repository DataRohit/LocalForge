"""Unit tests for RabbitMQ delayed-delivery exchange preparation.

Exercises the exact topology, declaration boundary, command wiring, and module entrypoint without
opening a real broker connection.
"""

from __future__ import annotations

import runpy
import sys
from unittest.mock import MagicMock, call, patch

import pytest
from scripts import prepare_broker

pytestmark = pytest.mark.unit
DELAYED_LEVEL_COUNT = 28


def test_exchange_names_cover_kombus_complete_delayed_topology() -> None:
    """Cover every exchange referenced by Kombu's native delayed queues.

    Requires all numbered levels in ascending dependency order followed by the terminal delivery
    exchange, with no duplicate name.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the topology no longer matches Kombu.
    """
    expected = (
        *(f"celery_delayed_{level}" for level in range(DELAYED_LEVEL_COUNT)),
        "celery_delayed_delivery",
    )

    assert expected == prepare_broker.DELAYED_EXCHANGE_NAMES
    assert len(set(expected)) == len(expected)


def test_declaration_creates_durable_topic_exchanges_and_closes_the_channel() -> None:
    """Declare the complete topology through one disposable channel.

    Replaces Kombu's exchange object so the test grades every name and option without requiring
    RabbitMQ, then confirms the channel is closed after success.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If declaration shape, order, return value, or cleanup drifts.
    """
    connection = MagicMock()
    channel = connection.channel.return_value
    bound = MagicMock()

    with patch.object(prepare_broker, "Exchange") as exchange:
        exchange.return_value.bind.return_value = bound
        assert (
            prepare_broker.declare_delayed_delivery_exchanges(connection)
            == prepare_broker.DELAYED_EXCHANGE_NAMES
        )

    assert exchange.call_args_list == [
        call(name, type="topic", durable=True) for name in prepare_broker.DELAYED_EXCHANGE_NAMES
    ]
    assert exchange.return_value.bind.call_args_list == [
        call(channel) for _name in prepare_broker.DELAYED_EXCHANGE_NAMES
    ]
    assert bound.declare.call_count == len(prepare_broker.DELAYED_EXCHANGE_NAMES)
    channel.close.assert_called_once_with()


def test_declaration_closes_the_channel_after_failure() -> None:
    """Close the broker channel when one exchange declaration fails.

    Fabricates a declaration error and verifies it remains visible.
    Confirms cleanup still occurs before the failure returns.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        RuntimeError: Fabricated broker declaration failure.
        AssertionError: If the channel remains open.
    """
    connection = MagicMock()
    channel = connection.channel.return_value
    bound = MagicMock()
    bound.declare.side_effect = RuntimeError("fabricated declaration failure")

    with patch.object(prepare_broker, "Exchange") as exchange:
        exchange.return_value.bind.return_value = bound
        with pytest.raises(RuntimeError, match="fabricated declaration failure"):
            prepare_broker.declare_delayed_delivery_exchanges(connection)

    channel.close.assert_called_once_with()


def test_main_connects_with_the_generated_broker_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run preparation through the command boundary.

    Replaces the Kombu connection and declaration function.
    Retains environment lookup so the generated URL remains required.

    Arguments:
        monkeypatch: Fixture setting the generated broker URL.

    Returns:
        None.

    Raises:
        AssertionError: If connection wiring or command status drifts.
    """
    monkeypatch.setenv("CELERY_BROKER_URL", "amqp://broker.invalid/vhost")
    context = MagicMock()
    connection = MagicMock()
    context.__enter__.return_value = connection

    with (
        patch.object(prepare_broker, "Connection", return_value=context) as connection_type,
        patch.object(prepare_broker, "declare_delayed_delivery_exchanges") as declare,
    ):
        assert prepare_broker.main() == 0

    connection_type.assert_called_once_with(
        "amqp://broker.invalid/vhost",
        connect_timeout=prepare_broker.BROKER_CONNECT_TIMEOUT_SECONDS,
    )
    declare.assert_called_once_with(connection)


def test_module_entrypoint_returns_main_status(monkeypatch: pytest.MonkeyPatch) -> None:
    """Execute the module guard without a real broker.

    Patches Kombu before module re-execution.
    Confirms successful preparation exits with the command status.

    Arguments:
        monkeypatch: Fixture setting the broker URL and module cache.

    Returns:
        None.

    Raises:
        AssertionError: If the module guard ignores main's status.
    """
    monkeypatch.setenv("CELERY_BROKER_URL", "amqp://broker.invalid/vhost")
    monkeypatch.delitem(sys.modules, "scripts.prepare_broker")
    context = MagicMock()
    channel = context.__enter__.return_value.channel.return_value

    with patch("kombu.Connection", return_value=context), patch("kombu.Exchange") as exchange:
        exchange.return_value.bind.return_value.declare.return_value = None
        with pytest.raises(SystemExit) as failure:
            runpy.run_module("scripts.prepare_broker", run_name="__main__")

    assert failure.value.code == 0
    channel.close.assert_called_once_with()
