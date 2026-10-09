"""WebSocket JSON web token authentication.

Classifies the token-bearing subprotocol or the browser access cookie, resolves current account
state on the primary database, and installs the user without exposing credentials to URLs or logs.
"""

from __future__ import annotations

import logging
from functools import partial
from http.cookies import SimpleCookie
from typing import TYPE_CHECKING, cast, override
from uuid import UUID

from channels.auth import AuthMiddleware
from channels.db import database_sync_to_async
from django.conf import settings
from django.db import Error as DjangoDatabaseError
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import ExpiredTokenError, TokenError
from rest_framework_simplejwt.settings import api_settings
from rest_framework_simplejwt.tokens import AccessToken
from rest_framework_simplejwt.utils import get_md5_hash_password

from accounts.models import User
from config.logs import REQUEST_ID_META_KEY
from notifications.protocol import (
    COOKIE_SOURCE,
    SUBPROTOCOL_SOURCE,
    WebSocketOutcome,
    close_rejected_handshake,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from asgiref.typing import (
        ASGIReceiveCallable,
        ASGISendCallable,
        Scope,
    )
    from rest_framework_simplejwt.tokens import Token

logger = logging.getLogger(__name__)


def access_cookie_from_scope(scope: Mapping[str, object]) -> str | None:
    """Read the browser access cookie from a handshake scope.

    Joins every cookie header, which HTTP/2 may split, parses them with the standard library, which
    skips malformed pairs, and treats a missing or empty value as no cookie.

    Arguments:
        scope: WebSocket connection scope.

    Returns:
        The encoded access token, or None when no usable cookie was sent.
    """
    values = [
        value.decode("latin-1")
        for name, value in cast("list[tuple[bytes, bytes]]", scope.get("headers", []))
        if name.lower() == b"cookie"
    ]
    jar: SimpleCookie = SimpleCookie()
    jar.load("; ".join(values))
    morsel = jar.get(cast("str", settings.BROWSER_ACCESS_COOKIE_NAME))

    return str(morsel.value) if morsel is not None and morsel.value else None


class WebSocketAuthenticationError(Exception):
    """Carry one client-visible authentication rejection classification.

    Inherits from ``Exception`` and stores only the central outcome, deliberately excluding the
    credential and package exception text so rejection handling cannot leak either into logs.

    Attributes:
        outcome: Central WebSocket failure outcome returned to the client.

    Members:
        None beyond those inherited from ``Exception``.
    """

    def __init__(self, outcome: WebSocketOutcome) -> None:
        """Create one safe authentication rejection.

        Retains the machine-readable close classification without an exception message, keeping
        encoded credentials and decoder diagnostics outside representations.

        Arguments:
            outcome: Central authentication rejection outcome.

        Returns:
            None.
        """
        super().__init__()
        self.outcome = outcome


class WebSocketJWTAuthentication(JWTAuthentication):
    """Validate access tokens and classify authoritative account state.

    Inherits from SimpleJWT's ``JWTAuthentication`` while preserving expired-token distinction and
    replacing replica-routable account lookup with an explicit primary query.

    Attributes:
        None beyond those inherited from ``JWTAuthentication``.

    Members:
        get_validated_token: Validate one access token with stable failure classification.
        get_user: Resolve its active current owner on the primary database.
    """

    @override
    def get_validated_token(self, raw_token: bytes) -> Token:
        """Validate one encoded access token.

        Uses SimpleJWT's configured access-token implementation while retaining expiration as a
        separate retryable outcome and normalizing every other token failure as malformed.

        Arguments:
            raw_token: Encoded token supplied as the sole WebSocket subprotocol.

        Returns:
            Validated access token.

        Raises:
            WebSocketAuthenticationError: If the credential expired or is unusable.
        """
        try:
            return AccessToken(cast("Token", raw_token))
        except ExpiredTokenError as error:
            expired = AccessToken(cast("Token", raw_token), verify=False)
            outcome = (
                WebSocketOutcome.CREDENTIAL_EXPIRED
                if expired.payload.get(api_settings.TOKEN_TYPE_CLAIM) == AccessToken.token_type
                else WebSocketOutcome.CREDENTIAL_MALFORMED
            )
            raise WebSocketAuthenticationError(outcome) from error
        except (OSError, OverflowError, TokenError, TypeError, ValueError) as error:
            raise WebSocketAuthenticationError(WebSocketOutcome.CREDENTIAL_MALFORMED) from error

    @override
    def get_user(self, validated_token: Token) -> User:  # type: ignore[override]
        """Resolve the token owner through authoritative primary state.

        Separates malformed identity, missing account, inactive account, and password-revoked
        credential outcomes so clients receive the documented close guidance.

        Arguments:
            validated_token: Access token whose signature and temporal claims are valid.

        Returns:
            Active account currently bound to the token.

        Raises:
            WebSocketAuthenticationError: If identity or account state rejects the credential.
            DatabaseError: If the primary account lookup fails.
        """
        user_id = validated_token.payload.get(api_settings.USER_ID_CLAIM)
        if not isinstance(user_id, str):
            raise WebSocketAuthenticationError(WebSocketOutcome.CREDENTIAL_MALFORMED)

        try:
            parsed_user_id = UUID(user_id)
        except ValueError as error:
            raise WebSocketAuthenticationError(WebSocketOutcome.CREDENTIAL_MALFORMED) from error

        try:
            user = User.objects.using("default").get(**{api_settings.USER_ID_FIELD: parsed_user_id})
        except User.DoesNotExist as error:
            raise WebSocketAuthenticationError(WebSocketOutcome.ACCOUNT_NOT_FOUND) from error

        if not user.is_active:
            raise WebSocketAuthenticationError(WebSocketOutcome.ACCOUNT_INACTIVE)

        expected_password_hash = get_md5_hash_password(user.password)
        if validated_token.payload.get(api_settings.REVOKE_TOKEN_CLAIM) != expected_password_hash:
            raise WebSocketAuthenticationError(WebSocketOutcome.CREDENTIAL_MALFORMED)

        return user


async def revalidate_session(
    authentication: WebSocketJWTAuthentication,
    token: Token,
) -> WebSocketOutcome | None:
    """Re-check the account behind an authenticated socket.

    Resolves the account on the primary again, so deactivation, deletion, and password replacement
    are noticed after the handshake.

    Arguments:
        authentication: Adapter owning authoritative account classification.
        token: Validated access token admitted at the handshake.

    Returns:
        The rejecting outcome, or None while the session remains valid.

    Raises:
        DatabaseError: If the primary lookup fails.
    """
    try:
        await database_sync_to_async(JWTSubprotocolAuthMiddleware.resolve_user)(
            authentication,
            token,
        )
    except WebSocketAuthenticationError as error:
        return error.outcome

    return None


class JWTSubprotocolAuthMiddleware(AuthMiddleware):  # type: ignore[misc]
    """Authenticate the sole WebSocket subprotocol or the access cookie as a JSON web token.

    Inherits from Channels' ``AuthMiddleware`` but replaces session resolution with the project's
    token protocol, installing the primary-resolved user, the credential source, and any accepted
    subprotocol in a copied scope. A handshake offering both sources is rejected, never merged.

    Attributes:
        None beyond those inherited from ``AuthMiddleware``.

    Members:
        __call__: Resolve authentication and delegate or close the connection.
        resolve_scope: Validate the credential and populate the copied scope.
        resolve_user: Resolve current account state synchronously for the async-safe wrapper.
    """

    @override
    async def __call__(
        self,
        scope: Scope,
        receive: ASGIReceiveCallable,
        send: ASGISendCallable,
    ) -> None:
        """Authenticate one connection before it reaches the protected router.

        Copies the scope, converts credential and account failures into their documented close
        codes, and treats primary database loss as a server failure rather than bad authentication.

        Arguments:
            scope: Incoming WebSocket connection scope.
            receive: Callable yielding connection events.
            send: Callable emitting connection events.

        Returns:
            None.
        """
        authenticated_scope = dict(cast("Mapping[str, object]", scope))
        try:
            await self.resolve_scope(authenticated_scope)
        except WebSocketAuthenticationError as error:
            await close_rejected_handshake(receive, send, error.outcome)
            return
        except DjangoDatabaseError:
            logger.log(
                logging.ERROR,
                "websocket authentication store unavailable",
                extra={
                    REQUEST_ID_META_KEY: cast(
                        "str",
                        authenticated_scope["connection_request_id"],
                    )
                },
            )
            await close_rejected_handshake(receive, send, WebSocketOutcome.SERVER_ERROR)
            return

        await self.inner(authenticated_scope, receive, send)

    @override
    async def resolve_scope(self, scope: dict[str, object]) -> None:
        """Validate the one credential source and install its active account.

        Rejects an absent or ambiguous credential before decoding, including a cookie offered
        beside a subprotocol, then runs the primary account lookup through the async wrapper.

        Arguments:
            scope: Copied mutable WebSocket scope receiving authentication state.

        Returns:
            None.

        Raises:
            WebSocketAuthenticationError: If the credential or account is rejected.
            DatabaseError: If the primary account lookup fails.
        """
        subprotocols = scope.get("subprotocols")
        cookie = access_cookie_from_scope(scope)
        offered = isinstance(subprotocols, list) and bool(subprotocols)
        if cookie is not None and offered:
            raise WebSocketAuthenticationError(WebSocketOutcome.CREDENTIAL_MALFORMED)
        if cookie is None and not offered:
            raise WebSocketAuthenticationError(WebSocketOutcome.CREDENTIAL_ABSENT)

        credential = cookie
        if credential is None:
            offered_protocols = cast("list[object]", subprotocols)
            first = offered_protocols[0]
            if len(offered_protocols) != 1 or not isinstance(first, str) or not first:
                raise WebSocketAuthenticationError(WebSocketOutcome.CREDENTIAL_MALFORMED)
            credential = first

        authentication = WebSocketJWTAuthentication()
        token = authentication.get_validated_token(credential.encode())
        user = await database_sync_to_async(self.resolve_user)(authentication, token)
        scope["user"] = user
        scope["access_token"] = token
        scope["revalidate_session"] = partial(revalidate_session, authentication, token)
        scope["credential_source"] = COOKIE_SOURCE if cookie is not None else SUBPROTOCOL_SOURCE
        if cookie is None:
            scope["accepted_subprotocol"] = credential

    @staticmethod
    def resolve_user(authentication: WebSocketJWTAuthentication, token: Token) -> User:
        """Resolve one validated token owner synchronously.

        Exists as the explicit database seam wrapped by ``database_sync_to_async``, allowing the
        middleware to prove no ORM operation blocks its event loop.

        Arguments:
            authentication: Adapter owning authoritative account classification.
            token: Validated access token naming the account.

        Returns:
            Active current account bound to the token.

        Raises:
            WebSocketAuthenticationError: If current account state rejects the token.
            DatabaseError: If the primary lookup fails.
        """
        return authentication.get_user(token)
