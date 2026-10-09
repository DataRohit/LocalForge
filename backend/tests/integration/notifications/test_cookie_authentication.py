"""Integration tests for WebSocket access-cookie authentication.

Exercises the deployed notification route with a browser handshake that carries the HttpOnly
access cookie, proving admission, the single-credential rule, every rejection classification,
revocation, expiry, and credential secrecy.
"""

import logging
from datetime import timedelta
from typing import TYPE_CHECKING, Any, cast

import pytest
from asgiref.sync import sync_to_async
from channels.db import database_sync_to_async
from django.conf import settings
from freezegun import freeze_time
from rest_framework_simplejwt.tokens import AccessToken

from accounts.jwt_authentication import PrimaryRefreshToken
from config.asgi import application
from config.logs import StructuredFormatter
from notifications.delivery import publish_notification, publish_session_revoked
from notifications.protocol import WebSocketOutcome
from tests.websocket import WebsocketCommunicator

if TYPE_CHECKING:
    from accounts.models import User

ROUTE = "/ws/notifications/"
ORIGIN_HEADERS = [(b"host", b"localhost"), (b"origin", b"http://localhost:8080")]
RECEIVE_TIMEOUT_SECONDS = 10
EXPIRY_SECONDS = 1
pytestmark = [
    pytest.mark.integration,
    pytest.mark.services("postgres", "valkey-channels"),
    pytest.mark.django_db(databases=["default", "replica"], transaction=True),
]


async def account_and_access(django_user_model: type[User], name: str) -> tuple[User, str]:
    """Create an account and issue its access token.

    Uses the primary-backed refresh adapter so the token carries the production identity and
    password-revocation claims.

    Arguments:
        django_user_model: Configured custom user model class.
        name: Unique username.

    Returns:
        The account and its encoded access token.
    """
    account = await database_sync_to_async(django_user_model.objects.create_user)(
        name,
        f"{name}@localforge.invalid",
        "valid-password",
        is_active=True,
    )
    refresh = await database_sync_to_async(PrimaryRefreshToken.for_user)(account)

    return cast("User", account), str(refresh.access_token)


def cookie_headers(
    value: str | None,
    *,
    origin: bytes | None = b"http://localhost:8080",
) -> list[tuple[bytes, bytes]]:
    """Build handshake headers carrying the access cookie.

    Builds the ASGI headers of a browser handshake.
    The cookie sits beside an unrelated one, as a real browser would send it.

    Arguments:
        value: Cookie value, or None to omit the cookie.
        origin: Origin header, or None to omit it.

    Returns:
        The ASGI header list.
    """
    headers = [(b"host", b"localhost")]
    if origin is not None:
        headers.append((b"origin", origin))
    if value is not None:
        name = settings.BROWSER_ACCESS_COOKIE_NAME
        headers.append((b"cookie", f"unrelated=1; {name}={value}".encode()))

    return headers


async def close_code_of(communicator: WebsocketCommunicator) -> int:
    """Read the close code of a rejected handshake and finish the connection.

    Reads the close event of a handshake the server rejected.
    Disconnects afterwards so no application task survives.

    Arguments:
        communicator: Communicator that has just connected.

    Returns:
        The private application close code.
    """
    output = await communicator.receive_output(timeout=RECEIVE_TIMEOUT_SECONDS)
    assert output["type"] == "websocket.close"
    await communicator.disconnect()

    return cast("int", output["code"])


@pytest.mark.asyncio
async def test_a_valid_cookie_connects_and_receives_user_events(
    django_user_model: type[User],
) -> None:
    """Admit a browser handshake and deliver a targeted notification.

    Connects with only the access cookie and an allowed origin.
    A published account event must then reach the socket.

    Arguments:
        django_user_model: Configured custom user model class.

    Returns:
        None.

    Raises:
        AssertionError: If the cookie handshake is refused or delivery fails.
    """
    account, access = await account_and_access(django_user_model, "ws-cookie-valid")
    communicator = WebsocketCommunicator(application, ROUTE, headers=cookie_headers(access))

    connected, accepted = await communicator.connect()
    await sync_to_async(publish_notification)(account.pk, "account.updated", {"revision": 2})
    frame = await communicator.receive_json_from(timeout=RECEIVE_TIMEOUT_SECONDS)

    assert connected
    assert accepted is None
    assert frame["payload"] == {"event": "account.updated", "data": {"revision": 2}}
    await communicator.disconnect()


@pytest.mark.asyncio
async def test_a_cookie_beside_a_subprotocol_is_rejected_never_merged(
    django_user_model: type[User],
) -> None:
    """Refuse a handshake that presents both credential sources.

    Offers the same token as a cookie and as a subprotocol.
    The server must close with the malformed-credential code.

    Arguments:
        django_user_model: Configured custom user model class.

    Returns:
        None.

    Raises:
        AssertionError: If either credential is silently preferred.
    """
    _account, access = await account_and_access(django_user_model, "ws-cookie-both")
    communicator = WebsocketCommunicator(
        application,
        ROUTE,
        headers=cookie_headers(access),
        subprotocols=[access],
    )

    connected, _accepted = await communicator.connect()

    assert connected
    assert await close_code_of(communicator) == WebSocketOutcome.CREDENTIAL_MALFORMED.close_code


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case", "outcome"),
    [
        ("malformed", WebSocketOutcome.CREDENTIAL_MALFORMED),
        ("expired", WebSocketOutcome.CREDENTIAL_EXPIRED),
        ("revoked", WebSocketOutcome.CREDENTIAL_MALFORMED),
        ("inactive", WebSocketOutcome.ACCOUNT_INACTIVE),
        ("deleted", WebSocketOutcome.ACCOUNT_NOT_FOUND),
        ("absent", WebSocketOutcome.CREDENTIAL_ABSENT),
    ],
)
async def test_an_unusable_cookie_closes_with_its_documented_code(
    django_user_model: type[User],
    case: str,
    outcome: WebSocketOutcome,
) -> None:
    """Classify each cookie defect exactly as the subprotocol path does.

    Corrupts, expires, revokes, or removes the account behind the cookie.
    Each defect must close exactly as the subprotocol path does.

    Arguments:
        django_user_model: Configured custom user model class.
        case: Which defect the cookie carries.
        outcome: Close outcome the server must deliver.

    Returns:
        None.

    Raises:
        AssertionError: If the close code differs.
    """
    account, access = await account_and_access(django_user_model, f"ws-cookie-{case}")
    value: str | None = access
    if case == "malformed":
        value = "not-a-token"
    elif case == "absent":
        value = None
    elif case == "revoked":
        account.set_password("replaced-password")
        await database_sync_to_async(account.save)()
    elif case == "inactive":
        await database_sync_to_async(
            django_user_model.objects.filter(pk=account.pk).update,
        )(is_active=False)
    elif case == "deleted":
        await database_sync_to_async(django_user_model.objects.filter(pk=account.pk).delete)()

    communicator = WebsocketCommunicator(application, ROUTE, headers=cookie_headers(value))
    if case == "expired":
        with freeze_time(timedelta(seconds=settings.JWT_ACCESS_TOKEN_LIFETIME_SECONDS + 5)):
            connected, _accepted = await communicator.connect()
            code = await close_code_of(communicator)
    else:
        connected, _accepted = await communicator.connect()
        code = await close_code_of(communicator)

    assert connected
    assert code == outcome.close_code


@pytest.mark.asyncio
@pytest.mark.parametrize("origin", [None, b"https://attacker.invalid"], ids=["absent", "foreign"])
async def test_a_cookie_handshake_from_a_disallowed_origin_never_reaches_the_consumer(
    django_user_model: type[User],
    origin: bytes | None,
) -> None:
    """Refuse cross-site WebSocket hijacking before authentication.

    Presents a valid cookie with a missing or foreign origin.
    The origin validator must close before authentication runs.

    Arguments:
        django_user_model: Configured custom user model class.
        origin: Origin header, or None to omit it.

    Returns:
        None.

    Raises:
        AssertionError: If the handshake reaches authentication or the consumer.
    """
    _account, access = await account_and_access(django_user_model, "ws-cookie-origin")
    communicator = WebsocketCommunicator(
        application,
        ROUTE,
        headers=cookie_headers(access, origin=origin),
    )

    connected, _accepted = await communicator.connect()

    assert connected
    assert await close_code_of(communicator) == WebSocketOutcome.PERMISSION_DENIED.close_code


@pytest.mark.asyncio
async def test_session_revocation_closes_cookie_sockets_but_not_bearer_sockets(
    django_user_model: type[User],
) -> None:
    """End browser sockets at logout and leave bearer sockets alone.

    Opens one cookie socket and one bearer socket for the same account.
    Publishing the revocation must close only the cookie socket.

    Arguments:
        django_user_model: Configured custom user model class.

    Returns:
        None.

    Raises:
        AssertionError: If the wrong sockets close or the close code differs.
    """
    account, access = await account_and_access(django_user_model, "ws-cookie-revoked")
    browser = WebsocketCommunicator(application, ROUTE, headers=cookie_headers(access))
    bearer = WebsocketCommunicator(
        application,
        ROUTE,
        headers=ORIGIN_HEADERS,
        subprotocols=[access],
    )
    await browser.connect()
    await bearer.connect()

    await sync_to_async(publish_session_revoked)(account.pk)

    output = await browser.receive_output(timeout=RECEIVE_TIMEOUT_SECONDS)
    assert output["type"] == "websocket.close"
    assert output["code"] == WebSocketOutcome.CREDENTIAL_EXPIRED.close_code
    await bearer.receive_nothing(timeout=0.5)
    await bearer.disconnect()
    await browser.disconnect()


@pytest.mark.asyncio
async def test_the_next_frame_after_deactivation_closes_the_cookie_socket(
    django_user_model: type[User],
) -> None:
    """Revalidate the account on every inbound frame.

    Deactivates the account behind an open cookie socket.
    The next inbound frame must close it with the inactive code.

    Arguments:
        django_user_model: Configured custom user model class.

    Returns:
        None.

    Raises:
        AssertionError: If a disabled account keeps its socket.
    """
    account, access = await account_and_access(django_user_model, "ws-cookie-frame")
    communicator = WebsocketCommunicator(application, ROUTE, headers=cookie_headers(access))
    await communicator.connect()
    await database_sync_to_async(django_user_model.objects.filter(pk=account.pk).update)(
        is_active=False
    )

    await communicator.send_json_to({"type": "subscribe", "payload": {}})

    output = await communicator.receive_output(timeout=RECEIVE_TIMEOUT_SECONDS)
    assert output["type"] == "websocket.close"
    assert output["code"] == WebSocketOutcome.ACCOUNT_INACTIVE.close_code
    await communicator.disconnect()


@pytest.mark.asyncio
async def test_a_cookie_socket_closes_when_its_access_token_expires(
    django_user_model: type[User],
) -> None:
    """Bound a cookie socket by the lifetime of its credential.

    Issues an access token that lives one second.
    The server must close the socket with the expired code on its own.

    Arguments:
        django_user_model: Configured custom user model class.

    Returns:
        None.

    Raises:
        AssertionError: If the socket outlives its access token.
    """
    account, _access = await account_and_access(django_user_model, "ws-cookie-expiry")
    refresh = await database_sync_to_async(PrimaryRefreshToken.for_user)(account)
    short = cast("Any", refresh.access_token)
    short.set_exp(lifetime=timedelta(seconds=EXPIRY_SECONDS))
    communicator = WebsocketCommunicator(application, ROUTE, headers=cookie_headers(str(short)))

    connected, _accepted = await communicator.connect()
    output = await communicator.receive_output(timeout=RECEIVE_TIMEOUT_SECONDS)

    assert connected
    assert output["type"] == "websocket.close"
    assert output["code"] == WebSocketOutcome.CREDENTIAL_EXPIRED.close_code
    await communicator.disconnect()


@pytest.mark.asyncio
async def test_no_credential_reaches_a_log_record(
    django_user_model: type[User],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Keep the cookie credential out of every log record.

    Connects with valid, forged, and malformed cookies at debug verbosity.
    No rendered log record may contain a token.

    Arguments:
        django_user_model: Configured custom user model class.
        caplog: Log capture fixture.

    Returns:
        None.

    Raises:
        AssertionError: If the token appears in a rendered record.
    """
    caplog.set_level(logging.DEBUG)
    _account, access = await account_and_access(django_user_model, "ws-cookie-logs")
    forged = str(AccessToken())
    for value in (access, forged, "not-a-token"):
        communicator = WebsocketCommunicator(application, ROUTE, headers=cookie_headers(value))
        await communicator.connect()
        await communicator.disconnect()

    rendered = "\n".join(StructuredFormatter().format(record) for record in caplog.records)
    assert access not in rendered
    assert forged not in rendered


@pytest.mark.asyncio
async def test_a_frame_from_a_still_valid_cookie_session_is_handled_normally(
    django_user_model: type[User],
) -> None:
    """Keep a healthy cookie socket open across inbound frames.

    Sends an unsupported message type over a valid cookie session and expects the recoverable
    error frame, proving revalidation passes while the account stays active.

    Arguments:
        django_user_model: Configured custom user model class.

    Returns:
        None.

    Raises:
        AssertionError: If a healthy session is closed or mishandled.
    """
    _account, access = await account_and_access(django_user_model, "ws-cookie-healthy")
    communicator = WebsocketCommunicator(application, ROUTE, headers=cookie_headers(access))
    await communicator.connect()

    await communicator.send_json_to({"type": "unsupported", "payload": {}})
    frame = await communicator.receive_json_from(timeout=RECEIVE_TIMEOUT_SECONDS)

    assert frame["payload"]["code"] == WebSocketOutcome.UNKNOWN_MESSAGE_TYPE.code
    await communicator.disconnect()
