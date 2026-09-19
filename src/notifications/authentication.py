"""WebSocket JSON web token authentication.

Classifies the token-bearing subprotocol, resolves current account state on the primary database,
and installs the authenticated user without exposing credentials to URLs or application logs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast, override
from uuid import UUID

from channels.auth import AuthMiddleware
from channels.db import database_sync_to_async
from django.db import Error as DjangoDatabaseError
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import ExpiredTokenError, TokenError
from rest_framework_simplejwt.settings import api_settings
from rest_framework_simplejwt.tokens import AccessToken
from rest_framework_simplejwt.utils import get_md5_hash_password

from accounts.models import User
from notifications.websocket import close_rejected_handshake

if TYPE_CHECKING:
    from collections.abc import Mapping

    from asgiref.typing import (
        ASGIReceiveCallable,
        ASGISendCallable,
        Scope,
    )
    from rest_framework_simplejwt.tokens import Token

CREDENTIAL_ABSENT_CLOSE_CODE = 4401
CREDENTIAL_MALFORMED_CLOSE_CODE = 4402
CREDENTIAL_EXPIRED_CLOSE_CODE = 4403
ACCOUNT_INACTIVE_CLOSE_CODE = 4404
ACCOUNT_NOT_FOUND_CLOSE_CODE = 4405
SERVER_ERROR_CLOSE_CODE = 4500


class WebSocketAuthenticationError(Exception):
    """Carry one client-visible authentication rejection classification.

    Inherits from ``Exception`` and stores only a close code, deliberately excluding the credential
    and package exception text so rejection handling cannot leak either into logs.

    Attributes:
        close_code: Private-use WebSocket close code returned to the client.

    Members:
        None beyond those inherited from ``Exception``.
    """

    def __init__(self, close_code: int) -> None:
        """Create one safe authentication rejection.

        Retains the machine-readable close classification without an exception message, keeping
        encoded credentials and decoder diagnostics outside representations.

        Arguments:
            close_code: Private-use close code for the rejected connection.

        Returns:
            None.
        """
        super().__init__()
        self.close_code = close_code


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
            close_code = (
                CREDENTIAL_EXPIRED_CLOSE_CODE
                if expired.payload.get(api_settings.TOKEN_TYPE_CLAIM) == AccessToken.token_type
                else CREDENTIAL_MALFORMED_CLOSE_CODE
            )
            raise WebSocketAuthenticationError(close_code) from error
        except (OSError, OverflowError, TokenError, TypeError, ValueError) as error:
            raise WebSocketAuthenticationError(CREDENTIAL_MALFORMED_CLOSE_CODE) from error

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
            raise WebSocketAuthenticationError(CREDENTIAL_MALFORMED_CLOSE_CODE)

        try:
            parsed_user_id = UUID(user_id)
        except ValueError as error:
            raise WebSocketAuthenticationError(CREDENTIAL_MALFORMED_CLOSE_CODE) from error

        try:
            user = User.objects.using("default").get(**{api_settings.USER_ID_FIELD: parsed_user_id})
        except User.DoesNotExist as error:
            raise WebSocketAuthenticationError(ACCOUNT_NOT_FOUND_CLOSE_CODE) from error

        if not user.is_active:
            raise WebSocketAuthenticationError(ACCOUNT_INACTIVE_CLOSE_CODE)

        expected_password_hash = get_md5_hash_password(user.password)
        if validated_token.payload.get(api_settings.REVOKE_TOKEN_CLAIM) != expected_password_hash:
            raise WebSocketAuthenticationError(CREDENTIAL_MALFORMED_CLOSE_CODE)

        return user


class JWTSubprotocolAuthMiddleware(AuthMiddleware):  # type: ignore[misc]
    """Authenticate the sole WebSocket subprotocol as a JSON web token.

    Inherits from Channels' ``AuthMiddleware`` but replaces session resolution with the project's
    token protocol, installing the primary-resolved user and accepted subprotocol in a copied scope.

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
            await close_rejected_handshake(receive, send, error.close_code)
            return
        except DjangoDatabaseError:
            await close_rejected_handshake(receive, send, SERVER_ERROR_CLOSE_CODE)
            return

        await self.inner(authenticated_scope, receive, send)

    @override
    async def resolve_scope(self, scope: dict[str, object]) -> None:
        """Validate the sole subprotocol and install its active account.

        Rejects an absent or ambiguous protocol list before decoding, then runs the synchronous
        primary account lookup through Channels' database-aware async wrapper.

        Arguments:
            scope: Copied mutable WebSocket scope receiving authentication state.

        Returns:
            None.

        Raises:
            WebSocketAuthenticationError: If the credential or account is rejected.
            DatabaseError: If the primary account lookup fails.
        """
        subprotocols = scope.get("subprotocols")
        if not isinstance(subprotocols, list) or not subprotocols:
            raise WebSocketAuthenticationError(CREDENTIAL_ABSENT_CLOSE_CODE)
        if len(subprotocols) != 1 or not isinstance(subprotocols[0], str) or not subprotocols[0]:
            raise WebSocketAuthenticationError(CREDENTIAL_MALFORMED_CLOSE_CODE)

        credential = subprotocols[0]
        authentication = WebSocketJWTAuthentication()
        token = authentication.get_validated_token(credential.encode())
        user = await database_sync_to_async(self.resolve_user)(authentication, token)
        scope["user"] = user
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
