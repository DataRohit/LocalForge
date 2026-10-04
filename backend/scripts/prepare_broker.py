"""Prepare RabbitMQ's native delayed-delivery exchanges.

Declares every exchange Kombu's quorum delayed-delivery topology references before any queue is
created, preventing fresh brokers from logging successful startup as missing-exchange warnings.
"""

from __future__ import annotations

import os

from kombu import Connection, Exchange
from kombu.transport.native_delayed_delivery import (
    CELERY_DELAYED_DELIVERY_EXCHANGE,
    MAX_LEVEL,
    level_name,
)

BROKER_CONNECT_TIMEOUT_SECONDS = 10
DELAYED_EXCHANGE_NAMES = (
    *(level_name(level) for level in range(MAX_LEVEL + 1)),
    CELERY_DELAYED_DELIVERY_EXCHANGE,
)


def declare_delayed_delivery_exchanges(connection: Connection) -> tuple[str, ...]:
    """Declare every delayed-delivery exchange before Kombu creates its queues.

    Uses durable topic exchanges matching Kombu's own topology so later declarations are
    idempotent and no queue temporarily references a missing dead-letter exchange.

    Arguments:
        connection: Open broker connection used to create one channel.

    Returns:
        Exchange names declared in dependency-safe order.
    """
    channel = connection.channel()
    try:
        for name in DELAYED_EXCHANGE_NAMES:
            Exchange(name, type="topic", durable=True).bind(channel).declare()
    finally:
        channel.close()

    return DELAYED_EXCHANGE_NAMES


def main() -> int:
    """Prepare delayed delivery through the generated broker connection.

    Reads the existing broker URL from the process environment and completes before the caller
    starts Django, Celery, or the persistent testing runner.

    Arguments:
        None.

    Returns:
        Zero after every exchange is present.

    Raises:
        KeyError: If the broker URL is absent.
        OSError: If the broker cannot be reached.
    """
    with Connection(
        os.environ["CELERY_BROKER_URL"],
        connect_timeout=BROKER_CONNECT_TIMEOUT_SECONDS,
    ) as connection:
        declare_delayed_delivery_exchanges(connection)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
