"""Integration tests for WebSocket JSON web token authentication.

Exercises the deployed notification route and the authentication middleware seam against primary
account state, proving subprotocol negotiation and every credential rejection classification.
"""

import json
import logging
from datetime import timedelta
from typing import TYPE_CHECKING, cast
from uuid import UUID

import pytest
from channels.db import database_sync_to_async
from django.conf import settings
from django.db import DatabaseError, InterfaceError
from django.utils import timezone
from freezegun import freeze_time
from rest_framework_simplejwt.tokens import AccessToken

from accounts.jwt_authentication import PrimaryRefreshToken
from config.asgi import application
from config.logs import REQUEST_ID_META_KEY
from notifications.authentication import JWTSubprotocolAuthMiddleware
from notifications.protocol import WebSocketOutcome
from notifications.websocket import ExactWebSocketHostValidator, ExactWebSocketOriginValidator
from tests.websocket import WebsocketCommunicator

if TYPE_CHECKING:
    from asgiref.typing import ASGIReceiveCallable, ASGISendCallable, ASGISendEvent, Scope

    from accounts.models import User

ROUTE = "/ws/notifications/"
HEADERS = [(b"host", b"localhost"), (b"origin", b"http://localhost:8080")]
RECEIVE_TIMEOUT_SECONDS = 10
pytestmark = [
    pytest.mark.integration,
    pytest.mark.services("postgres", "valkey-channels"),
    pytest.mark.django_db(databases=["default", "replica"], transaction=True),
]


async def issue_access_token(account: User) -> str:
    """Issue one project access token for an account.

    Uses the same primary-backed refresh adapter as the REST create endpoint so the WebSocket
    credential carries the production identity and password-revocation claims.

    Arguments:
        account: Active account receiving the credential.

    Returns:
        Encoded short-lived access token.
    """
    refresh = await database_sync_to_async(PrimaryRefreshToken.for_user)(account)

    return str(refresh.access_token)


async def connect_with_subprotocols(
    subprotocols: list[str] | None,
) -> tuple[WebsocketCommunicator, bool, str | int | None]:
    """Connect to the deployed notification route with optional credentials.

    Crosses origin, host, authentication, routing, and consumer admission in production order so
    close-code assertions observe the public handshake rather than an internal helper.

    Arguments:
        subprotocols: Protocol values offered by the connecting client.

    Returns:
        Communicator, connection result, and accepted subprotocol or rejection detail.
    """
    communicator = WebsocketCommunicator(
        application,
        ROUTE,
        headers=HEADERS,
        subprotocols=subprotocols,
    )
    connected, detail = await communicator.connect()

    return communicator, connected, detail


async def assert_rejected(
    subprotocols: list[str] | None,
    outcome: WebSocketOutcome,
) -> None:
    """Assert one credential shape closes with its documented code.

    Observes the minimal accepted handshake followed by the private-use close frame, then completes
    the peer disconnect so no application task survives the test.

    Arguments:
        subprotocols: Protocol values offered by the rejected client.
        outcome: Central close outcome the server must deliver.

    Returns:
        None.

    Raises:
        AssertionError: If the handshake or close classification differs.
    """
    communicator, connected, accepted = await connect_with_subprotocols(subprotocols)

    assert connected
    assert accepted is None
    output = await communicator.receive_output(timeout=RECEIVE_TIMEOUT_SECONDS)
    assert output["type"] == "websocket.close"
    assert output["code"] == outcome.required_close_code()
    await communicator.disconnect()


@pytest.mark.asyncio
async def test_valid_access_token_is_echoed_and_connection_is_accepted(
    django_user_model: type[User],
) -> None:
    """Authenticate the notification socket with the REST access credential.

    Confirms the token-bearing subprotocol is echoed exactly and the deployed consumer remains
    usable after authentication.

    Arguments:
        django_user_model: Configured account model used to create the token owner.

    Returns:
        None.

    Raises:
        AssertionError: If authentication or subprotocol negotiation fails.
    """
    account = await database_sync_to_async(django_user_model.objects.create_user)(
        "websocket-valid",
        "websocket-valid@localforge.invalid",
        "valid-password",
        is_active=True,
    )
    access = await issue_access_token(account)
    communicator, connected, accepted = await connect_with_subprotocols([access])

    try:
        assert connected
        assert accepted == access
        await communicator.send_json_to({"type": "unsupported", "payload": {}})
        response = await communicator.receive_json_from(timeout=RECEIVE_TIMEOUT_SECONDS)
        assert response["payload"]["code"] == WebSocketOutcome.UNKNOWN_MESSAGE_TYPE.code
    finally:
        await communicator.disconnect()


@pytest.mark.asyncio
async def test_missing_and_malformed_credentials_are_distinct() -> None:
    """Classify absent and unusable subprotocol credentials separately.

    Covers no protocol, malformed text, and an ambiguous protocol list so clients can decide
    whether to obtain a credential or replace one they already hold.

    Arguments:
        None.

    Returns:
        None.
    """
    await assert_rejected(None, WebSocketOutcome.CREDENTIAL_ABSENT)
    await assert_rejected(
        ["not-a-json-web-token"],
        WebSocketOutcome.CREDENTIAL_MALFORMED,
    )
    await assert_rejected(["first", "second"], WebSocketOutcome.CREDENTIAL_MALFORMED)


@pytest.mark.asyncio
async def test_signed_malformed_account_claims_are_rejected() -> None:
    """Reject signed access tokens without a usable account identifier.

    Covers a missing claim and a non-UUID claim so signed attacker-controlled protocol metadata
    cannot fall through to Django field coercion or become an unknown-account classification.

    Arguments:
        None.

    Returns:
        None.
    """
    missing_identity = AccessToken()
    malformed_identity = AccessToken()
    malformed_identity["user_id"] = "not-a-uuid"

    await assert_rejected([str(missing_identity)], WebSocketOutcome.CREDENTIAL_MALFORMED)
    await assert_rejected([str(malformed_identity)], WebSocketOutcome.CREDENTIAL_MALFORMED)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "headers",
    [
        [(b"origin", b"http://localhost:8080")],
        [(b"host", b"untrusted.invalid"), (b"origin", b"http://localhost:8080")],
        [
            (b"host", b"localhost"),
            (b"host", b"localhost"),
            (b"origin", b"http://localhost:8080"),
        ],
    ],
    ids=["absent", "unlisted", "repeated"],
)
async def test_host_rejection_precedes_credential_processing(
    headers: list[tuple[bytes, bytes]],
) -> None:
    """Reject invalid Host shapes before inspecting authentication.

    Connects without a credential and expects authorization rather than credential absence,
    proving host admission is the outer boundary and cannot leak token parsing behavior.

    Arguments:
        headers: ASGI headers describing the rejected Host shape.

    Returns:
        None.

    Raises:
        AssertionError: If the connection reaches authentication or uses another close code.
    """
    communicator = WebsocketCommunicator(application, ROUTE, headers=headers)
    connected, accepted = await communicator.connect()

    assert connected
    assert accepted is None
    output = await communicator.receive_output(timeout=RECEIVE_TIMEOUT_SECONDS)
    assert output["code"] == WebSocketOutcome.PERMISSION_DENIED.required_close_code()
    await communicator.disconnect()


@pytest.mark.asyncio
async def test_authenticated_user_is_available_in_the_inner_scope(
    django_user_model: type[User],
) -> None:
    """Install the active account in the scope every consumer receives.

    Wraps a minimal consumer adapter with the production middleware and observes the copied scope,
    proving callers receive the model instance and echoed credential without reaching internals.

    Arguments:
        django_user_model: Configured account model used to create the token owner.

    Returns:
        None.

    Raises:
        AssertionError: If the middleware omits or substitutes authentication state.
    """
    account = await database_sync_to_async(django_user_model.objects.create_user)(
        "websocket-scope",
        "websocket-scope@localforge.invalid",
        "valid-password",
        is_active=True,
    )
    access = await issue_access_token(account)
    observed_scope: dict[str, object] = {}

    async def scope_probe(
        scope: Scope,
        receive: ASGIReceiveCallable,
        send: ASGISendCallable,
    ) -> None:
        """Expose authentication state through a minimal ASGI consumer.

        Copies the values under test, accepts with the middleware-selected subprotocol, and waits
        for peer disconnect so the communicator can cleanly finish.

        Arguments:
            scope: Authenticated WebSocket scope supplied by the middleware.
            receive: ASGI receive callable.
            send: ASGI send callable.

        Returns:
            None.
        """
        scope_values = cast("dict[str, object]", scope)
        observed_scope.update(scope_values)
        await receive()
        await send(
            cast(
                "ASGISendEvent",
                {
                    "type": "websocket.accept",
                    "subprotocol": scope_values["accepted_subprotocol"],
                },
            )
        )
        await send(
            cast(
                "ASGISendEvent",
                {
                    "type": "websocket.send",
                    "text": json.dumps({"user_id": str(cast("User", scope_values["user"]).pk)}),
                },
            )
        )
        await receive()

    protected = ExactWebSocketHostValidator(
        ExactWebSocketOriginValidator(JWTSubprotocolAuthMiddleware(scope_probe))
    )
    communicator = WebsocketCommunicator(
        protected,
        ROUTE,
        headers=HEADERS,
        subprotocols=[access],
    )
    connected, accepted = await communicator.connect()
    response = await communicator.receive_json_from(timeout=RECEIVE_TIMEOUT_SECONDS)

    assert connected
    assert accepted == access
    assert observed_scope["user"] == account
    assert response == {"user_id": str(account.pk)}
    await communicator.disconnect()


@pytest.mark.asyncio
async def test_expired_access_token_has_its_own_close_code(
    django_user_model: type[User],
) -> None:
    """Distinguish an expired access token from a malformed credential.

    Issues at a fixed instant and connects after expiry so the client can refresh authentication
    rather than discarding an otherwise valid credential shape.

    Arguments:
        django_user_model: Configured account model used to create the token owner.

    Returns:
        None.
    """
    account = await database_sync_to_async(django_user_model.objects.create_user)(
        "websocket-expired",
        "websocket-expired@localforge.invalid",
        "valid-password",
        is_active=True,
    )
    issued_at = timezone.now()
    with freeze_time(issued_at):
        access = await issue_access_token(account)

    with freeze_time(issued_at + timedelta(seconds=settings.JWT_ACCESS_TOKEN_LIFETIME_SECONDS + 1)):
        await assert_rejected([access], WebSocketOutcome.CREDENTIAL_EXPIRED)


@pytest.mark.asyncio
async def test_expired_refresh_token_remains_a_wrong_type(
    django_user_model: type[User],
) -> None:
    """Classify an expired refresh token as malformed rather than expired access.

    Advances beyond the refresh lifetime so expiration validation runs before token-type validation,
    then verifies the client still receives replace-credential guidance.

    Arguments:
        django_user_model: Configured account model used to create the token owner.

    Returns:
        None.
    """
    account = await database_sync_to_async(django_user_model.objects.create_user)(
        "websocket-expired-refresh",
        "websocket-expired-refresh@localforge.invalid",
        "valid-password",
        is_active=True,
    )
    issued_at = timezone.now()
    with freeze_time(issued_at):
        refresh = await database_sync_to_async(PrimaryRefreshToken.for_user)(account)

    with freeze_time(
        issued_at + timedelta(seconds=settings.JWT_REFRESH_TOKEN_LIFETIME_SECONDS + 1)
    ):
        await assert_rejected([str(refresh)], WebSocketOutcome.CREDENTIAL_MALFORMED)


@pytest.mark.asyncio
async def test_inactive_and_deleted_accounts_are_distinct(
    django_user_model: type[User],
) -> None:
    """Classify inactive and missing token owners independently.

    Reuses valid signed credentials while changing only authoritative primary account state,
    proving the middleware does not trust token claims as current authorization.

    Arguments:
        django_user_model: Configured account model used to create token owners.

    Returns:
        None.
    """
    inactive = await database_sync_to_async(django_user_model.objects.create_user)(
        "websocket-inactive",
        "websocket-inactive@localforge.invalid",
        "valid-password",
        is_active=True,
    )
    inactive_access = await issue_access_token(inactive)
    inactive.is_active = False
    await database_sync_to_async(inactive.save)(
        using="default",
        update_fields=["is_active"],
    )

    deleted = await database_sync_to_async(django_user_model.objects.create_user)(
        "websocket-deleted",
        "websocket-deleted@localforge.invalid",
        "valid-password",
        is_active=True,
    )
    deleted_access = await issue_access_token(deleted)
    await database_sync_to_async(deleted.delete)(using="default")

    await assert_rejected([inactive_access], WebSocketOutcome.ACCOUNT_INACTIVE)
    await assert_rejected([deleted_access], WebSocketOutcome.ACCOUNT_NOT_FOUND)


@pytest.mark.asyncio
async def test_revoked_access_and_refresh_credentials_are_malformed(
    django_user_model: type[User],
) -> None:
    """Reject a password-revoked access token and a refresh token as unusable.

    Confirms only current access credentials can authenticate the socket and both dead credential
    forms share the replace-credential guidance rather than pretending they are expired.

    Arguments:
        django_user_model: Configured account model used to create the token owner.

    Returns:
        None.
    """
    account = await database_sync_to_async(django_user_model.objects.create_user)(
        "websocket-revoked",
        "websocket-revoked@localforge.invalid",
        "valid-password",
        is_active=True,
    )
    refresh = await database_sync_to_async(PrimaryRefreshToken.for_user)(account)
    access = str(refresh.access_token)
    await database_sync_to_async(account.set_password)("replacement-password")
    await database_sync_to_async(account.save)(using="default", update_fields=["password"])

    await assert_rejected([access], WebSocketOutcome.CREDENTIAL_MALFORMED)
    await assert_rejected([str(refresh)], WebSocketOutcome.CREDENTIAL_MALFORMED)


@pytest.mark.asyncio
async def test_query_string_credential_is_never_used(
    django_user_model: type[User],
) -> None:
    """Reject a valid token supplied only in the URL query string.

    Proves proxy and browser-history-visible query parameters never become an authentication
    fallback, even when their value would succeed through the subprotocol header.

    Arguments:
        django_user_model: Configured account model used to create the token owner.

    Returns:
        None.

    Raises:
        AssertionError: If a query-string token authenticates the connection.
    """
    account = await database_sync_to_async(django_user_model.objects.create_user)(
        "websocket-query",
        "websocket-query@localforge.invalid",
        "valid-password",
        is_active=True,
    )
    access = await issue_access_token(account)
    communicator = WebsocketCommunicator(
        application,
        f"{ROUTE}?token={access}",
        headers=HEADERS,
    )
    connected, accepted = await communicator.connect()

    assert connected
    assert accepted is None
    output = await communicator.receive_output(timeout=RECEIVE_TIMEOUT_SECONDS)
    assert output["code"] == WebSocketOutcome.CREDENTIAL_ABSENT.required_close_code()
    await communicator.disconnect()


@pytest.mark.asyncio
async def test_credential_never_enters_application_logs(
    django_user_model: type[User],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Keep the subprotocol credential out of every application log record.

    Connects once successfully and once after revocation while capturing the complete application
    log stream, proving neither acceptance nor rejection serializes the bearer value.

    Arguments:
        django_user_model: Configured account model used to create the token owner.
        caplog: Fixture capturing application log records.

    Returns:
        None.

    Raises:
        AssertionError: If the encoded credential appears in a log message or attribute.
    """
    account = await database_sync_to_async(django_user_model.objects.create_user)(
        "websocket-log-redaction",
        "websocket-log-redaction@localforge.invalid",
        "valid-password",
        is_active=True,
    )
    access = await issue_access_token(account)

    with caplog.at_level(logging.DEBUG):
        communicator, connected, accepted = await connect_with_subprotocols([access])
        assert connected
        assert accepted == access
        await communicator.disconnect()

        await database_sync_to_async(account.set_password)("replacement-password")
        await database_sync_to_async(account.save)(
            using="default",
            update_fields=["password"],
        )
        await assert_rejected([access], WebSocketOutcome.CREDENTIAL_MALFORMED)

    assert access not in caplog.text
    assert all(access not in repr(record.__dict__) for record in caplog.records)


@pytest.mark.asyncio
@pytest.mark.parametrize("error_type", [DatabaseError, InterfaceError])
async def test_primary_lookup_failure_is_a_server_error(
    django_user_model: type[User],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    error_type: type[Exception],
) -> None:
    """Separate authoritative database loss from credential rejection.

    Replaces only the async-safe user resolution seam after token validation and verifies the
    connection receives the server-error code rather than instructions to replace its credential.

    Arguments:
        django_user_model: Configured account model used to create the token owner.
        monkeypatch: Fixture inducing the primary lookup failure.
        caplog: Fixture capturing the correlated failure record.
        error_type: Django database exception raised by the lookup seam.

    Returns:
        None.
    """
    account = await database_sync_to_async(django_user_model.objects.create_user)(
        "websocket-database-error",
        "websocket-database-error@localforge.invalid",
        "valid-password",
        is_active=True,
    )
    access = await issue_access_token(account)

    def fail_lookup(_authentication: object, _token: object) -> None:
        """Raise the authoritative lookup failure under test.

        Replaces only user resolution while leaving subprotocol parsing and token validation on
        their production path.

        Arguments:
            _authentication: Authentication adapter receiving the call.
            _token: Validated access token naming the account.

        Returns:
            Never returns.

        Raises:
            DatabaseError: Always.
        """
        raise error_type

    monkeypatch.setattr(
        JWTSubprotocolAuthMiddleware,
        "resolve_user",
        staticmethod(fail_lookup),
    )

    with caplog.at_level(logging.ERROR):
        await assert_rejected([access], WebSocketOutcome.SERVER_ERROR)

    records = [
        record
        for record in caplog.records
        if record.getMessage() == "websocket authentication store unavailable"
    ]
    assert len(records) == 1
    UUID(cast("str", records[0].__dict__[REQUEST_ID_META_KEY]))
    assert records[0].exc_info is None
    assert access not in caplog.text
    assert "Traceback" not in caplog.text
