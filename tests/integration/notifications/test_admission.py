"""Integration tests for shared WebSocket connection admission.

Exercises the dedicated Valkey store across exact windows and process boundaries so every Django
worker applies one authenticated-user connection budget.
"""

import asyncio
import logging
import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, cast
from unittest.mock import AsyncMock

import pytest
from channels.db import database_sync_to_async
from redis import asyncio as aioredis

from accounts.jwt_authentication import PrimaryRefreshToken
from config.asgi import application
from config.logs import REQUEST_ID_META_KEY
from notifications import admission
from notifications.admission import (
    WebSocketAdmissionStoreError,
    WebSocketConnectionAdmissionStore,
)
from notifications.delivery import notification_group_name
from notifications.protocol import WebSocketOutcome
from tests.websocket import WebsocketCommunicator

if TYPE_CHECKING:
    from typing import Protocol

    from accounts.models import User

    class MutableWebSocketSettings(Protocol):
        """Describe settings adjusted by admission integration tests.

        Inherits from ``Protocol`` and exposes only the connection rate and timeout adjusted by
        focused public-seam tests.

        Attributes:
            WEBSOCKET_CONNECTION_THROTTLE_RATE: Configured count-per-period admission rate.
            WEBSOCKET_CONNECTION_ADMISSION_TIMEOUT_SECONDS: Admission operation timeout.

        Members:
            None.
        """

        WEBSOCKET_CONNECTION_THROTTLE_RATE: str
        WEBSOCKET_CONNECTION_ADMISSION_TIMEOUT_SECONDS: float


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
ROUTE = "/ws/notifications/"
HEADERS = [(b"host", b"localhost"), (b"origin", b"http://localhost:8080")]
RECEIVE_TIMEOUT_SECONDS = 10
PROCESS_TIMEOUT_SECONDS = 60
MINUTE_SECONDS = 60
DEFAULT_CONNECTION_LIMIT = 30
WINDOW_EXPIRY_GRACE_MILLISECONDS = 1000

PROCESS_ADMISSIONS = """
import asyncio
import sys
import uuid

import django

django.setup()

from notifications.admission import WebSocketConnectionAdmissionStore


async def admit() -> None:
    store = WebSocketConnectionAdmissionStore.from_settings()
    user_id = uuid.UUID(sys.argv[1])
    count = int(sys.argv[2])
    now_milliseconds = int(sys.argv[3])
    decisions = [
        await store.admit(
            user_id,
            limit=30,
            window_seconds=60,
            now_milliseconds=now_milliseconds,
        )
        for _ in range(count)
    ]
    print(sum(decision.admitted for decision in decisions), flush=True)


asyncio.run(admit())
"""

pytestmark = [
    pytest.mark.integration,
    pytest.mark.services("valkey-channels"),
]


async def issue_access_token(account: User) -> str:
    """Issue one REST-compatible access credential.

    Uses the same primary-backed token adapter as the public endpoint so deployed admission tests
    cross authentication before charging the shared connection budget.

    Arguments:
        account: Active account receiving the credential.

    Returns:
        Encoded access token.
    """
    refresh = await database_sync_to_async(PrimaryRefreshToken.for_user)(account)

    return str(refresh.access_token)


async def connect_with_token(access: str) -> tuple[WebsocketCommunicator, bool, str | int | None]:
    """Open one deployed notification connection.

    Presents the token through the sole subprotocol and returns the complete handshake result so
    tests can distinguish admission from its private close frame.

    Arguments:
        access: Encoded access token to offer.

    Returns:
        Communicator, connection result, and accepted subprotocol or rejection detail.
    """
    communicator = WebsocketCommunicator(
        application,
        ROUTE,
        headers=HEADERS,
        subprotocols=[access],
    )
    connected, detail = await communicator.connect()

    return communicator, connected, detail


@pytest.mark.asyncio
async def test_connection_admission_enforces_and_resets_one_exact_window() -> None:
    """Admit the configured count, reject the next attempt, and reset at the boundary.

    Supplies coordinated server-time values to the real Valkey script so the count, retry delay,
    and epoch-aligned cleanup semantics are deterministic without waiting one minute.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If count, rejection, retry timing, or next-window cleanup differs.
    """
    store = WebSocketConnectionAdmissionStore.from_settings()
    user_id = uuid.uuid4()

    first = await store.admit(
        user_id,
        limit=2,
        window_seconds=60,
        now_milliseconds=60_000,
    )
    second = await store.admit(
        user_id,
        limit=2,
        window_seconds=60,
        now_milliseconds=60_000,
    )
    rejected = await store.admit(
        user_id,
        limit=2,
        window_seconds=60,
        now_milliseconds=60_000,
    )
    next_window = await store.admit(
        user_id,
        limit=2,
        window_seconds=60,
        now_milliseconds=120_000,
    )
    key = f"{store.prefix}:websocket-admission:{user_id.hex}"
    async with aioredis.Redis(
        host=store.host,
        port=store.port,
        password=store.password,
    ) as client:
        remaining_ttl_milliseconds = await client.pttl(key)

    assert first.admitted
    assert second.admitted
    assert not rejected.admitted
    assert rejected.retry_after_seconds == MINUTE_SECONDS
    assert next_window.admitted
    assert next_window.retry_after_seconds == 0
    assert (
        0 < remaining_ttl_milliseconds <= MINUTE_SECONDS * 1000 + WINDOW_EXPIRY_GRACE_MILLISECONDS
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("limit", "window_seconds"),
    [(0, MINUTE_SECONDS), (1, 0)],
    ids=["limit", "window"],
)
async def test_connection_admission_rejects_invalid_store_parameters(
    limit: int,
    window_seconds: int,
) -> None:
    """Reject invalid store parameters before opening a network connection.

    Exercises both non-positive inputs accepted by the method signature so configuration or caller
    defects cannot reach the shared store.

    Arguments:
        limit: Candidate admission count.
        window_seconds: Candidate fixed-window duration.

    Returns:
        None.

    Raises:
        AssertionError: If a non-positive value reaches Valkey.
    """
    store = WebSocketConnectionAdmissionStore.from_settings()

    with pytest.raises(
        ValueError,
        match="connection admission limit and window must be positive",
    ):
        await store.admit(
            uuid.uuid4(),
            limit=limit,
            window_seconds=window_seconds,
        )


@pytest.mark.asyncio
async def test_connection_admission_wraps_real_store_unavailability() -> None:
    """Hide the Redis client failure behind the stable store exception.

    Connects to a closed loopback port with a tight timeout so the real adapter failure path is
    covered without disturbing either configured Valkey service.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If a driver or operating-system exception escapes.
    """
    configured_store = WebSocketConnectionAdmissionStore.from_settings()
    store = WebSocketConnectionAdmissionStore(
        host="127.0.0.1",
        port=1,
        password=configured_store.password,
        prefix="localforge-test",
        socket_timeout_seconds=0.01,
    )

    with pytest.raises(WebSocketAdmissionStoreError):
        await store.admit(
            uuid.uuid4(),
            limit=1,
            window_seconds=MINUTE_SECONDS,
        )


@pytest.mark.asyncio
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
async def test_deployed_connection_admission_is_shared_and_resets(
    django_user_model: type[User],
    settings: MutableWebSocketSettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Apply the authenticated-user budget before consumer group membership.

    Uses two admissions in one fixed minute, observes the exact third-connection throttle close,
    then advances coordinated store time and proves the next minute admits again.

    Arguments:
        django_user_model: Configured account model used to create the token owner.
        settings: Django settings fixture used to select a small deterministic rate.
        monkeypatch: Fixture coordinating Valkey server time for this test.

    Returns:
        None.

    Raises:
        AssertionError: If authentication ordering, shared count, close code, or reset differs.
    """
    settings.WEBSOCKET_CONNECTION_THROTTLE_RATE = "2/minute"
    monkeypatch.setattr(admission, "TEST_SERVER_TIME_MILLISECONDS", 60_000)
    account = await database_sync_to_async(django_user_model.objects.create_user)(
        "websocket-admission",
        "websocket-admission@localforge.invalid",
        "valid-password",
        is_active=True,
    )
    access = await issue_access_token(account)

    for _ in range(2):
        communicator, connected, accepted = await connect_with_token(access)
        assert connected
        assert accepted == access
        await communicator.disconnect()

    rejected, connected, accepted = await connect_with_token(access)
    assert connected
    assert accepted is None
    output = await rejected.receive_output(timeout=RECEIVE_TIMEOUT_SECONDS)
    assert output["code"] == WebSocketOutcome.CONNECTION_THROTTLED.required_close_code()
    await rejected.disconnect()
    store = WebSocketConnectionAdmissionStore.from_settings()
    group_channel = f"{store.prefix}__group__{notification_group_name(account.pk)}"
    async with aioredis.Redis(
        host=store.host,
        port=store.port,
        password=store.password,
    ) as client:
        subscribers = await client.pubsub_numsub(group_channel)
    assert subscribers[0][1] == 0

    monkeypatch.setattr(admission, "TEST_SERVER_TIME_MILLISECONDS", 120_000)
    next_window, connected, accepted = await connect_with_token(access)
    assert connected
    assert accepted == access
    await next_window.disconnect()


@pytest.mark.asyncio
async def test_connection_admission_is_shared_with_another_process() -> None:
    """Share one account budget between independent operating-system processes.

    Records half the admissions in a child interpreter and half in this process at one coordinated
    time, then proves the next attempt sees the combined count and is rejected.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If process-local state admits the thirty-first attempt.
    """
    user_id = uuid.uuid4()
    now_milliseconds = 3_600_000
    environment = {
        **os.environ,
        "PYTHONPATH": f"{REPOSITORY_ROOT / 'src'}{os.pathsep}{REPOSITORY_ROOT}",
    }
    child = await asyncio.to_thread(
        subprocess.run,
        [
            sys.executable,
            "-c",
            PROCESS_ADMISSIONS,
            str(user_id),
            "15",
            str(now_milliseconds),
        ],
        capture_output=True,
        text=True,
        cwd=REPOSITORY_ROOT,
        env=environment,
        timeout=PROCESS_TIMEOUT_SECONDS,
        check=False,
    )
    assert child.returncode == 0, child.stderr
    assert child.stdout.strip() == "15"

    store = WebSocketConnectionAdmissionStore.from_settings()
    local = [
        await store.admit(
            user_id,
            limit=30,
            window_seconds=60,
            now_milliseconds=now_milliseconds,
        )
        for _ in range(15)
    ]
    rejected = await store.admit(
        user_id,
        limit=30,
        window_seconds=60,
        now_milliseconds=now_milliseconds,
    )

    assert all(decision.admitted for decision in local)
    assert not rejected.admitted
    assert rejected.retry_after_seconds == MINUTE_SECONDS


@pytest.mark.asyncio
async def test_connection_admission_is_atomic_under_concurrency() -> None:
    """Admit exactly thirty of thirty-one concurrent attempts.

    Starts every decision together against one account and coordinated minute, proving the shared
    Lua script cannot oversubscribe the configured default count under worker contention.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the atomic store admits too many or rejects too early.
    """
    store = WebSocketConnectionAdmissionStore.from_settings()
    user_id = uuid.uuid4()
    decisions = await asyncio.gather(
        *(
            store.admit(
                user_id,
                limit=30,
                window_seconds=MINUTE_SECONDS,
                now_milliseconds=7_200_000,
            )
            for _ in range(31)
        )
    )

    assert sum(decision.admitted for decision in decisions) == DEFAULT_CONNECTION_LIMIT
    assert sum(not decision.admitted for decision in decisions) == 1
    assert {decision.retry_after_seconds for decision in decisions if not decision.admitted} == {
        MINUTE_SECONDS
    }


@pytest.mark.asyncio
async def test_authentication_rejection_precedes_connection_admission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject a missing credential without charging the shared account store.

    Replaces the admission seam and connects through the full deployed stack, proving host, origin,
    and authentication complete before any account budget can be touched.

    Arguments:
        monkeypatch: Fixture observing the admission seam.

    Returns:
        None.

    Raises:
        AssertionError: If admission runs or authentication uses another close outcome.
    """
    admit = AsyncMock()
    monkeypatch.setattr(admission, "admit_websocket_connection", admit)
    communicator = WebsocketCommunicator(application, ROUTE, headers=HEADERS)
    connected, accepted = await communicator.connect()

    assert connected
    assert accepted is None
    output = await communicator.receive_output(timeout=RECEIVE_TIMEOUT_SECONDS)
    assert output["code"] == WebSocketOutcome.CREDENTIAL_ABSENT.required_close_code()
    admit.assert_not_awaited()
    await communicator.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
async def test_connection_admission_store_failure_fails_closed_and_is_correlated(
    django_user_model: type[User],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Close with server error when shared admission state is unavailable.

    Raises the stable store adapter error after authentication and verifies the failure is logged
    with one UUID request identifier without driver text, traceback, or credential disclosure.

    Arguments:
        django_user_model: Configured account model used to create the token owner.
        monkeypatch: Fixture replacing the admission seam.
        caplog: Fixture capturing application log records.

    Returns:
        None.

    Raises:
        AssertionError: If store loss admits, leaks, or uses another close outcome.
    """
    account = await database_sync_to_async(django_user_model.objects.create_user)(
        "websocket-store-failure",
        "websocket-store-failure@localforge.invalid",
        "valid-password",
        is_active=True,
    )
    access = await issue_access_token(account)
    monkeypatch.setattr(
        admission,
        "admit_websocket_connection",
        AsyncMock(side_effect=WebSocketAdmissionStoreError),
    )

    with caplog.at_level(logging.ERROR):
        communicator, connected, accepted = await connect_with_token(access)
        assert connected
        assert accepted is None
        output = await communicator.receive_output(timeout=RECEIVE_TIMEOUT_SECONDS)
        assert output["code"] == WebSocketOutcome.SERVER_ERROR.required_close_code()
        await communicator.disconnect()

    records = [
        record
        for record in caplog.records
        if record.getMessage() == "websocket connection admission unavailable"
    ]
    assert len(records) == 1
    request_id = records[0].__dict__[REQUEST_ID_META_KEY]
    uuid.UUID(cast("str", request_id))
    assert records[0].exc_info is None
    assert access not in caplog.text
    assert "Traceback" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
async def test_connection_admission_timeout_fails_closed_and_cancels_work(
    django_user_model: type[User],
    settings: MutableWebSocketSettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Close with server error when shared admission exceeds its time budget.

    Blocks the admission coroutine, configures a tight public timeout, and proves ``wait_for``
    cancels the unfinished operation before the protected consumer can start.

    Arguments:
        django_user_model: Configured account model used to create the token owner.
        settings: Django settings fixture carrying the timeout.
        monkeypatch: Fixture replacing admission with cancellable work.

    Returns:
        None.

    Raises:
        AssertionError: If timeout admits, leaves work running, or uses another close outcome.
    """
    account = await database_sync_to_async(django_user_model.objects.create_user)(
        "websocket-store-timeout",
        "websocket-store-timeout@localforge.invalid",
        "valid-password",
        is_active=True,
    )
    access = await issue_access_token(account)
    cancelled = asyncio.Event()

    async def block_admission(_user_id: uuid.UUID) -> None:
        """Block until timeout cancellation arrives.

        Waits indefinitely and records the cancellation injected by the middleware timeout before
        propagating it to ``wait_for``.

        Arguments:
            _user_id: Authenticated account identifier.

        Returns:
            Never returns normally.

        Raises:
            CancelledError: Re-raised after recording cancellation.
        """
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    settings.WEBSOCKET_CONNECTION_ADMISSION_TIMEOUT_SECONDS = 0.01
    monkeypatch.setattr(admission, "admit_websocket_connection", block_admission)
    communicator, connected, accepted = await connect_with_token(access)

    assert connected
    assert accepted is None
    output = await communicator.receive_output(timeout=RECEIVE_TIMEOUT_SECONDS)
    assert output["code"] == WebSocketOutcome.SERVER_ERROR.required_close_code()
    assert cancelled.is_set()
    await communicator.disconnect()
