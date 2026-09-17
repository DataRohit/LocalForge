"""JSON web token authentication endpoints.

Issues, rotates, and validates short-lived access credentials and longer-lived refresh credentials
through the fixed versioned API while preserving the shared account and error contracts.
"""

# mypy: disable-error-code=misc

from http import HTTPStatus
from typing import TYPE_CHECKING, cast, override

from django.db import DatabaseError, transaction
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.serializers import CharField, Serializer
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.settings import api_settings
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
from rest_framework_simplejwt.tokens import RefreshToken, Token, UntypedToken
from rest_framework_simplejwt.utils import datetime_from_epoch, get_md5_hash_password

from accounts.authentication import active_token_user
from accounts.token_authentication import (
    ErrorEnvelopeSerializer,
    TokenLoginSerializer,
    TokenLoginThrottle,
    error_example,
    error_response,
    lock_authenticated_account,
    verify_login_credentials,
)
from config.api_errors import (
    AUTHENTICATION_FAILED,
    INTERNAL_SERVER_ERROR,
    METHOD_NOT_ALLOWED,
    NOT_ACCEPTABLE,
    PARSE_ERROR,
    REQUEST_TOO_LARGE,
    SERVICE_UNAVAILABLE,
    THROTTLED,
    UNSUPPORTED_MEDIA_TYPE,
    VALIDATION_ERROR,
    ServiceUnavailable,
)

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractBaseUser
    from rest_framework.request import Request

    from accounts.models import User


class JWTCreateResponseSerializer(Serializer):
    """Describe a successful JSON web token credential exchange.

    Inherits from DRF's ``Serializer`` and exposes only the two credentials the protocol requires,
    keeping account attributes and other personal data outside the response.

    Attributes:
        access: Short-lived bearer credential.
        refresh: Longer-lived rotation credential.

    Members:
        None beyond those inherited from ``Serializer``.
    """

    access = CharField(read_only=True)
    refresh = CharField(read_only=True)


class JWTRefreshRequestSerializer(Serializer):
    """Validate a refresh-token exchange request.

    Inherits from DRF's ``Serializer`` and accepts only the credential being rotated, keeping the
    original value write-only so it cannot enter a rendered success response.

    Attributes:
        refresh: Refresh credential supplied by the client.

    Members:
        None beyond those inherited from ``Serializer``.
    """

    refresh = CharField(write_only=True)


class JWTRefreshResponseSerializer(Serializer):
    """Describe a successful refresh-token rotation.

    Inherits from DRF's ``Serializer`` and exposes the new access credential together with the
    rotated refresh credential that replaces the now-blacklisted input.

    Attributes:
        access: Newly issued short-lived bearer credential.
        refresh: Newly issued refresh credential.

    Members:
        None beyond those inherited from ``Serializer``.
    """

    access = CharField(read_only=True)
    refresh = CharField(read_only=True)


class JWTVerifyRequestSerializer(Serializer):
    """Validate a token verification request.

    Inherits from DRF's ``Serializer`` and accepts one credential without ever serializing it back
    to the caller.

    Attributes:
        token: Access or refresh credential whose validity is being checked.

    Members:
        None beyond those inherited from ``Serializer``.
    """

    token = CharField(write_only=True)


class PrimaryRefreshToken(RefreshToken):
    """Bind refresh-token blacklist state to the authoritative database.

    Inherits from SimpleJWT's ``RefreshToken`` while replacing every outstanding and blacklist
    lookup or write with an explicit primary connection.

    Attributes:
        None beyond those inherited from ``RefreshToken``.

    Members:
        check_blacklist: Reject a token already revoked on the primary.
        for_user: Issue a refresh token and persist its outstanding record on the primary.
    """

    @override
    def check_blacklist(self) -> None:
        """Reject a token whose identifier is blacklisted on the primary.

        Bypasses ordinary read routing so rotation is visible immediately and a lagging replica
        cannot admit replay.

        Arguments:
            None.

        Returns:
            None.

        Raises:
            TokenError: If the token identifier is invalid or already blacklisted.
        """
        jti = self.payload.get(api_settings.JTI_CLAIM)
        if not isinstance(jti, str) or not jti:
            raise TokenError

        if BlacklistedToken.objects.using("default").filter(token__jti=jti).exists():
            raise TokenError

    @classmethod
    @override
    def for_user(  # type: ignore[override]
        cls,
        user: AbstractBaseUser,
    ) -> PrimaryRefreshToken:
        """Issue a refresh token and record it on the primary.

        Adds the configured identity and password-revocation security claims, then persists the
        outstanding credential before returning it so subsequent rotation is authoritative
        immediately.

        Arguments:
            user: Active account receiving the credential.

        Returns:
            Newly issued refresh token.

        Raises:
            DatabaseError: If the outstanding token cannot be persisted.
        """
        account = cast("User", user)
        token = cls()
        token[api_settings.USER_ID_CLAIM] = str(getattr(user, api_settings.USER_ID_FIELD))
        token[api_settings.REVOKE_TOKEN_CLAIM] = get_md5_hash_password(user.password)
        OutstandingToken.objects.using("default").create(
            user=account,
            jti=token[api_settings.JTI_CLAIM],
            token=str(token),
            created_at=token.current_time,
            expires_at=datetime_from_epoch(token["exp"]),
        )

        return token


def rotate_refresh_token(encoded: str) -> dict[str, str]:
    """Rotate one refresh token atomically on the primary.

    Locks the account before its outstanding credential, checks revocation, then blacklists the
    original and persists its replacement in the same transaction so concurrent replay has one
    winner without opposing password-replacement lock order.

    Arguments:
        encoded: Refresh token supplied by the client.

    Returns:
        New access and refresh token strings.

    Raises:
        AuthenticationFailed: If the token, its revocation state, or its account is invalid.
        ServiceUnavailable: If the authoritative database cannot complete rotation.
    """
    try:
        with transaction.atomic(using="default"):
            try:
                refresh = PrimaryRefreshToken(cast("Token", encoded))
            except (TypeError, OverflowError, OSError) as error:
                raise AuthenticationFailed from error
            user = active_token_user(refresh, for_update=True)
            outstanding = (
                OutstandingToken.objects.using("default")
                .select_for_update()
                .get(
                    jti=refresh[api_settings.JTI_CLAIM],
                    user=user,
                )
            )
            if BlacklistedToken.objects.using("default").filter(token=outstanding).exists():
                raise AuthenticationFailed

            access = str(refresh.access_token)
            BlacklistedToken.objects.using("default").create(token=outstanding)
            replacement = PrimaryRefreshToken.for_user(user)
    except (OutstandingToken.DoesNotExist, TokenError, ValueError) as error:
        raise AuthenticationFailed from error
    except DatabaseError as error:
        raise ServiceUnavailable from error

    return {"access": access, "refresh": str(replacement)}


def verify_token(encoded: str) -> None:
    """Validate one token without returning its contents.

    Checks signature, expiry, token type, primary blacklist state, and current account state while
    exposing only success or the shared generic authentication failure.

    Arguments:
        encoded: Access or refresh token supplied by the client.

    Returns:
        None.

    Raises:
        AuthenticationFailed: If the token or its account is invalid.
        ServiceUnavailable: If the authoritative database cannot be read.
    """
    try:
        try:
            token = UntypedToken(cast("Token", encoded))
        except (TypeError, OverflowError, OSError) as error:
            raise AuthenticationFailed from error
        token_type = token.payload.get(api_settings.TOKEN_TYPE_CLAIM)
        if not isinstance(token_type, str) or token_type not in {"access", "refresh"}:
            raise AuthenticationFailed
        jti = cast("str", token.payload[api_settings.JTI_CLAIM])
        if BlacklistedToken.objects.using("default").filter(token__jti=jti).exists():
            raise AuthenticationFailed
        active_token_user(token)
    except (TokenError, ValueError) as error:
        raise AuthenticationFailed from error
    except DatabaseError as error:
        raise ServiceUnavailable from error


class JWTCreateView(APIView):
    """Issue access and refresh credentials to an active account.

    Inherits from DRF's ``APIView`` and reuses the authoritative credential verifier and admission
    policy, making rejected account states indistinguishable from unknown credentials.

    Attributes:
        authentication_classes: Empty because callers exchange credentials for their first token.
        permission_classes: Public access required for credential exchange.
        throttle_classes: Shared strict login admission policy.

    Members:
        get_authenticate_header: Preserve the bearer challenge on credential failure.
        post: Validate credentials and issue the token pair.
    """

    authentication_classes: tuple[type, ...] = ()
    permission_classes = (AllowAny,)
    throttle_classes = (TokenLoginThrottle,)

    @override
    def get_authenticate_header(self, request: Request) -> str:
        """Return the primary bearer challenge for credential failures.

        Keeps rejected username-and-password exchanges at unauthorized while this public view
        deliberately skips request authentication.

        Arguments:
            request: Credential exchange request being challenged.

        Returns:
            Stable bearer scheme name.
        """
        del request

        return "Bearer"

    @extend_schema(
        operation_id="jwt_create",
        summary="Exchange credentials for JSON web tokens",
        description=(
            "Authenticates an active account without revealing whether a rejected username "
            "exists. The response contains only a short-lived access token and a longer-lived "
            "refresh token."
        ),
        request=TokenLoginSerializer,
        auth=[],
        responses={
            HTTPStatus.OK: OpenApiResponse(
                response=JWTCreateResponseSerializer,
                description="Credentials were accepted and both token types are returned.",
                examples=[
                    OpenApiExample(
                        "Tokens issued",
                        value={"access": "<JWT>", "refresh": "<JWT>"},
                        response_only=True,
                    )
                ],
            ),
            HTTPStatus.BAD_REQUEST: OpenApiResponse(
                response=ErrorEnvelopeSerializer,
                description="The request body is malformed or misses a required credential field.",
                examples=[
                    error_example("Malformed JSON", PARSE_ERROR),
                    error_example(
                        "Missing username",
                        VALIDATION_ERROR,
                        details={"username": ["This field is required."]},
                    ),
                ],
            ),
            HTTPStatus.UNAUTHORIZED: error_response(
                "Credentials were rejected without disclosing account state.",
                "Authentication failed",
                AUTHENTICATION_FAILED,
            ),
            HTTPStatus.METHOD_NOT_ALLOWED: error_response(
                "The JSON web token create endpoint accepts only POST.",
                "Method not allowed",
                METHOD_NOT_ALLOWED,
            ),
            HTTPStatus.NOT_ACCEPTABLE: error_response(
                "The requested response representation is unavailable.",
                "Not acceptable",
                NOT_ACCEPTABLE,
            ),
            HTTPStatus.CONTENT_TOO_LARGE: error_response(
                "The request body exceeds the environment-configured API limit.",
                "Request too large",
                REQUEST_TOO_LARGE,
            ),
            HTTPStatus.UNSUPPORTED_MEDIA_TYPE: error_response(
                "The submitted request representation is unsupported.",
                "Unsupported media type",
                UNSUPPORTED_MEDIA_TYPE,
            ),
            HTTPStatus.TOO_MANY_REQUESTS: error_response(
                "The client address or account identifier exceeded its configured login rate.",
                "Too many requests",
                THROTTLED,
            ),
            HTTPStatus.INTERNAL_SERVER_ERROR: error_response(
                "An unexpected server failure was contained.",
                "Internal server error",
                INTERNAL_SERVER_ERROR,
            ),
            HTTPStatus.SERVICE_UNAVAILABLE: error_response(
                "Authoritative account, login-admission, or token-persistence state is "
                "unavailable.",
                "Service unavailable",
                SERVICE_UNAVAILABLE,
            ),
        },
    )
    def post(self, request: Request) -> Response:
        """Validate credentials and return a JSON web token pair.

        Applies field validation and the bounded password policy before minting credentials, so
        inactive, unknown, and reset-required accounts receive the same generic rejection.

        Arguments:
            request: REST request carrying username and password fields.

        Returns:
            Successful response containing access and refresh tokens.

        Raises:
            AuthenticationFailed: If the submitted credentials cannot authenticate.
            ServiceUnavailable: If authoritative account, throttle, or token state is unavailable.
            ValidationError: If the submitted fields are invalid.
        """
        serializer = TokenLoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            snapshot = verify_login_credentials(
                cast("str", serializer.validated_data["username"]),
                cast("str", serializer.validated_data["password"]),
            )
            with transaction.atomic(using="default"):
                account = lock_authenticated_account(snapshot)
                refresh = PrimaryRefreshToken.for_user(account)
        except DatabaseError as error:
            raise ServiceUnavailable from error

        return Response(
            {"access": str(refresh.access_token), "refresh": str(refresh)},
            status=HTTPStatus.OK,
        )


class JWTRefreshView(APIView):
    """Rotate a valid refresh credential.

    Inherits from DRF's ``APIView`` and opens only the credential-exchange boundary, while primary
    account and blacklist reads make deletion, deactivation, and replay immediately visible.

    Attributes:
        authentication_classes: Empty because the refresh credential is carried in the body.
        permission_classes: Public access required for refresh exchange.

    Members:
        get_authenticate_header: Preserve the bearer challenge on token failure.
        post: Validate and atomically rotate one refresh token.
    """

    authentication_classes: tuple[type, ...] = ()
    permission_classes = (AllowAny,)

    @override
    def get_authenticate_header(self, request: Request) -> str:
        """Return the bearer challenge for a rejected refresh token.

        Keeps all invalid, expired, revoked, and account-state failures at unauthorized while this
        public view skips header authentication.

        Arguments:
            request: Refresh request being challenged.

        Returns:
            Stable bearer scheme name.
        """
        del request

        return "Bearer"

    @extend_schema(
        operation_id="jwt_refresh",
        summary="Rotate a JSON web refresh token",
        description=(
            "Exchanges one valid refresh token for new access and refresh credentials. The "
            "account is locked before outstanding-token state, and the supplied refresh token is "
            "blacklisted atomically before its replacement is persisted."
        ),
        request=JWTRefreshRequestSerializer,
        auth=[],
        responses={
            HTTPStatus.OK: OpenApiResponse(
                response=JWTRefreshResponseSerializer,
                description="The refresh credential was rotated.",
                examples=[
                    OpenApiExample(
                        "Tokens rotated",
                        value={"access": "<JWT>", "refresh": "<JWT>"},
                        response_only=True,
                    )
                ],
            ),
            HTTPStatus.BAD_REQUEST: OpenApiResponse(
                response=ErrorEnvelopeSerializer,
                description="The request body is malformed or misses the refresh field.",
                examples=[
                    error_example("Malformed JSON", PARSE_ERROR),
                    error_example(
                        "Missing refresh",
                        VALIDATION_ERROR,
                        details={"refresh": ["This field is required."]},
                    ),
                ],
            ),
            HTTPStatus.UNAUTHORIZED: error_response(
                "The refresh token is expired, malformed, has invalid protocol claims, is revoked "
                "or blacklisted, or has no active account.",
                "Authentication failed",
                AUTHENTICATION_FAILED,
            ),
            HTTPStatus.METHOD_NOT_ALLOWED: error_response(
                "The JSON web token refresh endpoint accepts only POST.",
                "Method not allowed",
                METHOD_NOT_ALLOWED,
            ),
            HTTPStatus.NOT_ACCEPTABLE: error_response(
                "The requested response representation is unavailable.",
                "Not acceptable",
                NOT_ACCEPTABLE,
            ),
            HTTPStatus.CONTENT_TOO_LARGE: error_response(
                "The request body exceeds the environment-configured API limit.",
                "Request too large",
                REQUEST_TOO_LARGE,
            ),
            HTTPStatus.UNSUPPORTED_MEDIA_TYPE: error_response(
                "The submitted request representation is unsupported.",
                "Unsupported media type",
                UNSUPPORTED_MEDIA_TYPE,
            ),
            HTTPStatus.INTERNAL_SERVER_ERROR: error_response(
                "An unexpected server failure was contained.",
                "Internal server error",
                INTERNAL_SERVER_ERROR,
            ),
            HTTPStatus.SERVICE_UNAVAILABLE: error_response(
                "Authoritative account, outstanding-token, or blacklist state is unavailable.",
                "Service unavailable",
                SERVICE_UNAVAILABLE,
            ),
        },
    )
    def post(self, request: Request) -> Response:
        """Validate and rotate one refresh token.

        Applies request validation before atomic primary-backed rotation and returns only the two
        replacement credentials.

        Arguments:
            request: REST request carrying the refresh field.

        Returns:
            Successful response containing replacement access and refresh tokens.

        Raises:
            AuthenticationFailed: If the refresh token cannot be accepted.
            ServiceUnavailable: If authoritative account or blacklist state is unavailable.
            ValidationError: If the submitted field is invalid.
        """
        serializer = JWTRefreshRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        return Response(
            rotate_refresh_token(cast("str", serializer.validated_data["refresh"])),
            status=HTTPStatus.OK,
        )


class JWTVerifyView(APIView):
    """Report whether an access or refresh token remains valid.

    Inherits from DRF's ``APIView`` and exposes validity only through status and an empty success
    object, while checking primary account and blacklist state.

    Attributes:
        authentication_classes: Empty because the inspected credential is carried in the body.
        permission_classes: Public access required for standalone token verification.

    Members:
        get_authenticate_header: Preserve the bearer challenge on token failure.
        post: Validate one token without returning claims.
    """

    authentication_classes: tuple[type, ...] = ()
    permission_classes = (AllowAny,)

    @override
    def get_authenticate_header(self, request: Request) -> str:
        """Return the bearer challenge for a rejected token.

        Keeps expired, malformed, revoked, and account-state failures at unauthorized while the
        verification input remains in the request body.

        Arguments:
            request: Verification request being challenged.

        Returns:
            Stable bearer scheme name.
        """
        del request

        return "Bearer"

    @extend_schema(
        operation_id="jwt_verify",
        summary="Verify a JSON web token",
        description=(
            "Reports whether an access or refresh token is currently valid without returning its "
            "identity or protocol claims."
        ),
        request=JWTVerifyRequestSerializer,
        auth=[],
        responses={
            HTTPStatus.OK: OpenApiResponse(
                response={
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
                description="The token is valid; no token contents are returned.",
                examples=[
                    OpenApiExample(
                        "Token valid",
                        value={},
                        response_only=True,
                    )
                ],
            ),
            HTTPStatus.BAD_REQUEST: OpenApiResponse(
                response=ErrorEnvelopeSerializer,
                description="The request body is malformed or misses the token field.",
                examples=[
                    error_example("Malformed JSON", PARSE_ERROR),
                    error_example(
                        "Missing token",
                        VALIDATION_ERROR,
                        details={"token": ["This field is required."]},
                    ),
                ],
            ),
            HTTPStatus.UNAUTHORIZED: error_response(
                "The token is expired, malformed, has invalid protocol claims, is revoked or "
                "blacklisted, or has no active account.",
                "Authentication failed",
                AUTHENTICATION_FAILED,
            ),
            HTTPStatus.METHOD_NOT_ALLOWED: error_response(
                "The JSON web token verify endpoint accepts only POST.",
                "Method not allowed",
                METHOD_NOT_ALLOWED,
            ),
            HTTPStatus.NOT_ACCEPTABLE: error_response(
                "The requested response representation is unavailable.",
                "Not acceptable",
                NOT_ACCEPTABLE,
            ),
            HTTPStatus.CONTENT_TOO_LARGE: error_response(
                "The request body exceeds the environment-configured API limit.",
                "Request too large",
                REQUEST_TOO_LARGE,
            ),
            HTTPStatus.UNSUPPORTED_MEDIA_TYPE: error_response(
                "The submitted request representation is unsupported.",
                "Unsupported media type",
                UNSUPPORTED_MEDIA_TYPE,
            ),
            HTTPStatus.INTERNAL_SERVER_ERROR: error_response(
                "An unexpected server failure was contained.",
                "Internal server error",
                INTERNAL_SERVER_ERROR,
            ),
            HTTPStatus.SERVICE_UNAVAILABLE: error_response(
                "Authoritative account or blacklist state is unavailable.",
                "Service unavailable",
                SERVICE_UNAVAILABLE,
            ),
        },
    )
    def post(self, request: Request) -> Response:
        """Validate one token and return no claims.

        Applies field validation, signature and expiry checks, primary blacklist lookup, and active
        account resolution before returning an empty success object.

        Arguments:
            request: REST request carrying the token field.

        Returns:
            Empty successful response when the token is valid.

        Raises:
            AuthenticationFailed: If the token cannot be accepted.
            ServiceUnavailable: If authoritative account or blacklist state is unavailable.
            ValidationError: If the submitted field is invalid.
        """
        serializer = JWTVerifyRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        verify_token(cast("str", serializer.validated_data["token"]))

        return Response({}, status=HTTPStatus.OK)
