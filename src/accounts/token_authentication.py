"""Token authentication HTTP endpoints.

Exchanges active-account credentials for DRF tokens and destroys those credentials on logout,
keeping all authentication failures indistinguishable while exposing only the fixed versioned
routes owned by Ticket 29.
"""

# mypy: disable-error-code=misc

from __future__ import annotations

import secrets
from collections.abc import Mapping
from hashlib import sha256
from http import HTTPStatus
from typing import TYPE_CHECKING, Any, cast, override

from django.conf import settings
from django.db import DatabaseError, transaction
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema
from rest_framework.authtoken.models import Token
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.serializers import CharField, DictField, Serializer
from rest_framework.throttling import BaseThrottle
from rest_framework.views import APIView

import accounts.credentials as credential_policy
from accounts.api_throttling import (
    AnonymousApiThrottle,
    AuthenticationRecoveryThrottle,
)
from accounts.authentication import PrimaryTokenAuthentication
from accounts.login_throttle import PostgresLoginThrottleStore, RollingWindowRule
from accounts.request_throttling import parse_throttle_rate, trusted_client_address
from config.api_errors import (
    AUTHENTICATION_FAILED,
    INTERNAL_SERVER_ERROR,
    METHOD_NOT_ALLOWED,
    NOT_ACCEPTABLE,
    NOT_AUTHENTICATED,
    PARSE_ERROR,
    REQUEST_TOO_LARGE,
    SERVICE_UNAVAILABLE,
    THROTTLED,
    UNSUPPORTED_MEDIA_TYPE,
    VALIDATION_ERROR,
    ErrorDefinition,
    ServiceUnavailable,
)
from config.logs import REQUEST_ID_META_KEY

if TYPE_CHECKING:
    from rest_framework.request import Request

SCHEMA_REQUEST_ID = "00000000-0000-4000-8000-000000000000"


class TokenResponseSerializer(Serializer):
    """Describe the successful token-login body.

    Inherits from DRF's ``Serializer`` and exposes only the credential clients present in the
    authorization header, preventing account data from becoming part of the login contract.

    Attributes:
        token: DRF token value issued to the account.

    Members:
        None beyond those inherited from ``Serializer``.
    """

    token = CharField(read_only=True)


class ErrorEnvelopeSerializer(Serializer):
    """Describe the shared correlated API error envelope.

    Inherits from DRF's ``Serializer`` and mirrors the stable fields produced by the central
    exception handler while allowing validation details to retain their field-keyed structure.

    Attributes:
        code: Stable machine-readable error code.
        message: Safe human-readable message.
        details: Per-field validation details or an empty mapping.
        request_id: Correlation identifier shared with the response header.

    Members:
        None beyond those inherited from ``Serializer``.
    """

    code = CharField(read_only=True)
    message = CharField(read_only=True)
    details = DictField(read_only=True)
    request_id = CharField(read_only=True)


def error_example(
    name: str,
    definition: ErrorDefinition,
    *,
    details: dict[str, object] | None = None,
) -> OpenApiExample:
    """Build one schema example from the central error vocabulary.

    Reuses the same code and message definitions as runtime responses, preventing documentation
    text from drifting while allowing a validation response to show its field detail.

    Arguments:
        name: Human-readable example name.
        definition: Central code and safe message pair.
        details: Optional field validation mapping.

    Returns:
        Response-only OpenAPI example.
    """
    return OpenApiExample(
        name,
        value={
            "code": definition.code.value,
            "message": definition.message,
            "details": details or {},
            "request_id": SCHEMA_REQUEST_ID,
        },
        response_only=True,
    )


def error_response(
    description: str,
    name: str,
    definition: ErrorDefinition,
) -> OpenApiResponse:
    """Build one documented API error response.

    Couples the shared envelope serializer to a concrete response example so every listed status
    shows clients the exact correlated body rather than only a component reference.

    Arguments:
        description: Client-facing explanation of the failure condition.
        name: Human-readable example name.
        definition: Central code and safe message pair.

    Returns:
        OpenAPI response carrying the shared envelope and one example.
    """
    return OpenApiResponse(
        response=ErrorEnvelopeSerializer,
        description=description,
        examples=[error_example(name, definition)],
    )


def normalized_login_username(data: object) -> str | None:
    """Normalize a raw username with the login serializer's public field contract.

    Runs only the declared username field so valid account identities are throttled even when
    another credential field is absent or invalid, while malformed bodies remain serializer-owned.

    Arguments:
        data: Parsed request body presented to the login throttle.

    Returns:
        The serializer-normalized username, or None when the body or field is invalid.
    """
    if not isinstance(data, Mapping):
        return None

    try:
        return cast(
            "str",
            TokenLoginSerializer().fields["username"].run_validation(data.get("username")),
        )
    except ValidationError:
        return None


class TokenLoginThrottle(BaseThrottle):
    """Enforce address and account login limits in one atomic decision.

    Inherits from DRF's ``BaseThrottle`` and delegates authoritative rolling-window state to the
    primary database beside account state, failing closed if PostgreSQL cannot decide.

    Attributes:
        retry_after_seconds: Server-derived delay after a rejected request.

    Members:
        allow_request: Atomically admit or reject both login dimensions.
        wait: Return the retry delay for DRF's response header.
    """

    retry_after_seconds: int | None = None

    @override
    def allow_request(self, request: Request, view: APIView) -> bool:
        """Atomically enforce every usable login dimension.

        Always limits the client address and adds an account rule only for non-empty string input,
        leaving non-object and invalid bodies to the serializer without unsafe mapping access.

        Arguments:
            request: Login request being admitted.
            view: Login view applying the throttle.

        Returns:
            Whether the authoritative shared database admitted the request.

        Raises:
            ServiceUnavailable: If authoritative shared throttle state is unavailable.
            ValueError: If a configured throttle rate is invalid.
        """
        del view
        address_limit, address_window = parse_throttle_rate(
            cast("str", settings.TOKEN_LOGIN_ADDRESS_THROTTLE_RATE)
        )
        address = sha256(trusted_client_address(request).encode()).hexdigest()
        rules = [
            RollingWindowRule(
                key=f"address:{address}",
                limit=address_limit,
                window_seconds=address_window,
            )
        ]
        try:
            username = normalized_login_username(request.data)

            if username is not None:
                account_limit, account_window = parse_throttle_rate(
                    cast("str", settings.TOKEN_LOGIN_ACCOUNT_THROTTLE_RATE)
                )
                rules.append(
                    RollingWindowRule(
                        key=f"account:{credential_policy.account_throttle_identity(username)}",
                        limit=account_limit,
                        window_seconds=account_window,
                    )
                )

            request_id = request.META.get(REQUEST_ID_META_KEY)
            correlation = request_id if isinstance(request_id, str) else "uncorrelated"
            member = f"{correlation}:{secrets.token_hex(16)}"
            decision = PostgresLoginThrottleStore(settings.LOGIN_THROTTLE_DATABASE_ALIAS).admit(
                tuple(rules), member=member
            )
        except DatabaseError as error:
            raise ServiceUnavailable from error

        self.retry_after_seconds = decision.retry_after_seconds

        return decision.admitted

    @override
    def wait(self) -> float | None:
        """Return the authoritative store's retry delay.

        Supplies DRF with the authoritative duration for ``Retry-After`` and returns no estimate
        before a denied decision has populated the value.

        Arguments:
            None.

        Returns:
            Retry delay in seconds, or None before a rejection.
        """
        return float(self.retry_after_seconds) if self.retry_after_seconds is not None else None


class StrictRequestSerializer(Serializer):
    """Reject every request key outside a serializer's declared contract.

    Inherits from DRF's ``Serializer`` and converts unknown keys into field-specific validation
    details before normal field validation, preventing silent identity-selector spoofing.

    Attributes:
        None beyond those inherited from ``Serializer``.

    Members:
        to_internal_value: Reject undeclared input keys.
    """

    @override
    def to_internal_value(self, data: object) -> dict[str, Any]:
        """Reject undeclared keys before validating declared fields.

        Preserves DRF's normal non-object handling and returns one stable detail entry for every
        unexpected mapping key.

        Arguments:
            data: Raw parsed request representation.

        Returns:
            Validated native field mapping.

        Raises:
            ValidationError: If the input carries an undeclared field.
        """
        if isinstance(data, Mapping):
            unexpected = sorted(str(key) for key in data if key not in self.fields)
            if unexpected:
                raise ValidationError(
                    {field: ["This field is not allowed."] for field in unexpected}
                )

        return cast("dict[str, Any]", super().to_internal_value(data))


class TokenLoginSerializer(StrictRequestSerializer):
    """Validate the two token-login credential fields.

    Inherits from ``StrictRequestSerializer`` and accepts only username and password, leaving
    account lookup and password verification to the view's credential verifier.

    Attributes:
        username: Account username supplied by the client.
        password: Raw password supplied by the client and never returned.

    Members:
        None beyond those inherited from ``Serializer``.
    """

    username = CharField(max_length=150)
    password = CharField(trim_whitespace=False, write_only=True)


class TokenLoginView(APIView):
    """Issue the secondary non-expiring token credential.

    Inherits from DRF's ``APIView`` and opens anonymous credential exchange. Tokens are plaintext
    table keys with one non-expiring value per account; password profiles outside the accepted
    policy receive the generic bounded unknown-account rejection and require reset.

    Attributes:
        authentication_classes: Empty because callers do not yet hold a usable credential.
        permission_classes: Public access needed to exchange credentials.
        throttle_classes: Strict PostgreSQL address and account admission plus shared anonymous and
            authentication scopes.

    Members:
        get_authenticate_header: Preserve unauthorized status without authenticating the request.
        post: Validate credentials and return the account's token.
    """

    authentication_classes: tuple[type, ...] = ()
    permission_classes = (AllowAny,)
    throttle_classes = (
        TokenLoginThrottle,
        AnonymousApiThrottle,
        AuthenticationRecoveryThrottle,
    )

    @override
    def get_authenticate_header(self, request: Request) -> str:
        """Return the primary authentication challenge for credential failures.

        Keeps rejected username-and-password exchanges at status 401 while deliberately skipping
        request authentication, so a stale authorization header cannot block a client before
        Ticket 30 explicitly replaces the placeholder authenticator.

        Arguments:
            request: Login request whose body credentials were rejected.

        Returns:
            Stable Bearer challenge matching the globally primary scheme.

        """
        del request

        return "Bearer"

    @extend_schema(
        operation_id="token_login",
        summary="Exchange credentials for a token",
        description=(
            "Authenticates an active account without revealing whether a rejected username exists. "
            "Stored password profiles outside the accepted bounded policy receive the same generic "
            "failure and fixed dummy work as an unknown account and require password reset. "
            "The returned DRF token is a single non-expiring credential stored in plaintext as its "
            "table key and should be used only where the secondary scheme is appropriate."
        ),
        request=TokenLoginSerializer,
        auth=[],
        responses={
            HTTPStatus.OK: OpenApiResponse(
                response=TokenResponseSerializer,
                description="Credentials were accepted and the account token is returned.",
                examples=[
                    OpenApiExample(
                        "Token issued",
                        value={"token": "0123456789abcdef0123456789abcdef01234567"},
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
                "The token login endpoint accepts only POST.",
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
        """Validate credentials and return the account's token.

        Applies field validation before account authentication and creates the single DRF token only
        after an active account passes password verification.

        Arguments:
            request: REST request carrying username and password fields.

        Returns:
            Successful response containing only the token value.

        Raises:
            AuthenticationFailed: If the submitted credentials cannot authenticate.
            ServiceUnavailable: If authoritative account or token persistence is unavailable.
            ValidationError: If the submitted fields are invalid.
        """
        serializer = TokenLoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            snapshot = credential_policy.verify_login_credentials(
                cast("str", serializer.validated_data["username"]),
                cast("str", serializer.validated_data["password"]),
            )
            with transaction.atomic(using="default"):
                account = credential_policy.lock_authenticated_account(snapshot)
                token, _created = Token.objects.using("default").get_or_create(user=account)
        except DatabaseError as error:
            raise ServiceUnavailable from error

        return Response({"token": token.key}, status=HTTPStatus.OK)


class TokenLogoutView(APIView):
    """Destroy the secondary token presented by the caller.

    Inherits from DRF's ``APIView`` and accepts only DRF token authentication, ensuring logout
    revokes the exact credential that admitted the request rather than pretending a JSON web token
    was invalidated.

    Attributes:
        authentication_classes: Primary-reading token authentication for this operation.
        permission_classes: Authenticated callers only.
        throttle_classes: Shared authentication and account-security scope.

    Members:
        post: Delete the presented token.
    """

    authentication_classes = (PrimaryTokenAuthentication,)
    permission_classes = (IsAuthenticated,)
    throttle_classes = (AuthenticationRecoveryThrottle,)

    @extend_schema(
        operation_id="token_logout",
        summary="Destroy the caller's token",
        description=(
            "Deletes the DRF token that authenticated this request. The credential is stored in "
            "plaintext as the authentication table key and otherwise remains non-expiring until "
            "this operation revokes it."
        ),
        request=None,
        responses={
            HTTPStatus.NO_CONTENT: OpenApiResponse(
                response=None,
                description="The presented token was destroyed.",
            ),
            HTTPStatus.UNAUTHORIZED: OpenApiResponse(
                response=ErrorEnvelopeSerializer,
                description="A token was absent, malformed, unknown, or already revoked.",
                examples=[
                    error_example("Credential absent", NOT_AUTHENTICATED),
                    error_example("Authentication failed", AUTHENTICATION_FAILED),
                ],
            ),
            HTTPStatus.METHOD_NOT_ALLOWED: error_response(
                "The token logout endpoint accepts only POST.",
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
            HTTPStatus.TOO_MANY_REQUESTS: error_response(
                "The client address or account exceeded the authentication scope.",
                "Too many requests",
                THROTTLED,
            ),
            HTTPStatus.INTERNAL_SERVER_ERROR: error_response(
                "An unexpected server failure was contained.",
                "Internal server error",
                INTERNAL_SERVER_ERROR,
            ),
            HTTPStatus.SERVICE_UNAVAILABLE: error_response(
                "Authoritative token or account state is unavailable.",
                "Service unavailable",
                SERVICE_UNAVAILABLE,
            ),
        },
    )
    def post(self, request: Request) -> Response:
        """Delete the token that authenticated the request.

        Removes the plaintext table key before returning, so the same authorization header fails on
        the caller's next request.

        Arguments:
            request: Authenticated REST request carrying a DRF token.

        Returns:
            Empty successful response.

        Raises:
            ServiceUnavailable: If token authentication or deletion loses the primary database.
        """
        token = cast("Token", request.auth)
        try:
            token.delete()
        except DatabaseError as error:
            raise ServiceUnavailable from error

        return Response(status=HTTPStatus.NO_CONTENT)
