"""Shared fixtures and guards for the unit layer.

Blocks every outbound connection a unit test could make, through Python's sockets and through the
database driver's own C client, so the layer's promise that it touches no external service is
enforced rather than merely stated.
"""

import socket
from typing import Any

import psycopg
import pytest


class NetworkAccessInUnitTestError(RuntimeError):
    """Raised when a unit test tries to reach something outside the process.

    Inherits from ``RuntimeError`` and carries only its message, which names what the test tried to
    reach so the offending call is identifiable from the failure alone.

    Members:
        None beyond those the base class defines.
    """


@pytest.fixture(autouse=True)
def _no_network_access(monkeypatch: pytest.MonkeyPatch) -> None:
    """Refuse outbound connections while a unit test runs.

    Replaces the socket connect calls and the database driver's connect with ones that raise, while
    letting the standard library build the loopback socket pair an event loop needs, so async unit
    tests still run and a test reaching for a real service fails immediately.

    Arguments:
        monkeypatch: Patching fixture supplied by the test framework, which restores the originals.

    Returns:
        None.

    Raises:
        None.
    """
    original_connect = socket.socket.connect
    original_socketpair = socket.socketpair
    permitted = {"depth": 0}

    def refuse(self: socket.socket, address: Any) -> None:  # noqa: ANN401
        """Refuse one connection attempt.

        Passes the call through while the standard library is building a socket pair for an event
        loop, and otherwise raises, naming the address the test reached for.

        Arguments:
            self: Socket the attempt was made on.
            address: Address the test tried to reach.

        Returns:
            None.

        Raises:
            NetworkAccessInUnitTestError: Unless a socket pair is being built.
        """
        if permitted["depth"]:
            original_connect(self, address)

            return

        message = f"a unit test may not reach {address}; move it to the integration layer"
        raise NetworkAccessInUnitTestError(message)

    def build_socket_pair(*arguments: Any, **keywords: Any) -> tuple[socket.socket, socket.socket]:  # noqa: ANN401
        """Build a connected socket pair for an event loop.

        Permits the loopback connection the standard library makes on platforms without a native
        socket pair, which is how an event loop wakes itself rather than a call to a service.

        Arguments:
            *arguments: Positional arguments for the standard implementation.
            **keywords: Keyword arguments for the standard implementation.

        Returns:
            The connected pair of sockets.

        Raises:
            None.
        """
        permitted["depth"] += 1
        try:
            return original_socketpair(*arguments, **keywords)
        finally:
            permitted["depth"] -= 1

    def refuse_database(*_arguments: Any, **_keywords: Any) -> None:  # noqa: ANN401
        """Refuse one database connection attempt.

        Raises rather than connecting, because the driver's C client opens its own socket and never
        passes through the Python calls the rest of this guard replaces.

        Arguments:
            *_arguments: Positional arguments the caller supplied.
            **_keywords: Keyword arguments the caller supplied.

        Returns:
            None.

        Raises:
            NetworkAccessInUnitTestError: Always.
        """
        message = "a unit test may not reach the database; move it to the integration layer"
        raise NetworkAccessInUnitTestError(message)

    monkeypatch.setattr(socket, "socketpair", build_socket_pair)
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(psycopg, "connect", refuse_database)
    monkeypatch.setattr(psycopg.Connection, "connect", refuse_database)
    monkeypatch.setattr(psycopg.AsyncConnection, "connect", refuse_database)
