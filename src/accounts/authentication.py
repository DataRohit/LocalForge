"""REST authentication scheme ordering.

Authenticates JSON web tokens and secondary DRF tokens against authoritative primary account
state, preventing replica lag from accepting deletion, deactivation, or credential revocation.
"""

# mypy: disable-error-code=misc

from typing import TYPE_CHECKING, cast, override
from uuid import UUID

from django.db import DatabaseError
from drf_spectacular.extensions import OpenApiAuthenticationExtension
from rest_framework.authentication import TokenAuthentication
from rest_framework.authtoken.models import Token
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.authentication import JWTAuthentication as SimpleJWTAuthentication
from rest_framework_simplejwt.exceptions import (
    AuthenticationFailed as SimpleJWTAuthenticationFailed,
)
from rest_framework_simplejwt.settings import api_settings

from accounts.models import User
from config.api_errors import ServiceUnavailable

if TYPE_CHECKING:
    from rest_framework.request import Request
    from rest_framework_simplejwt.tokens import Token as JWTToken


def active_token_user(validated_token: JWTToken) -> User:
    """Resolve an active token owner through a parsed protocol identity.

    Parses the signed identity as the UUID this protocol issues before querying the authoritative
    database, keeping malformed claims outside Django field coercion and sharing one lookup across
    body-carried and bearer credentials.

    Arguments:
        validated_token: Validated token carrying the configured account identity claim.

    Returns:
        Active account named by the token.

    Raises:
        AuthenticationFailed: If identity is absent, malformed, unknown, or inactive.
        DatabaseError: If the primary cannot be read.
    """
    user_id = validated_token.payload.get(api_settings.USER_ID_CLAIM)
    if not isinstance(user_id, str):
        raise AuthenticationFailed

    try:
        parsed_user_id = UUID(user_id)
    except ValueError as error:
        raise AuthenticationFailed from error

    try:
        user = User.objects.using("default").get(**{api_settings.USER_ID_FIELD: parsed_user_id})
    except User.DoesNotExist as error:
        raise AuthenticationFailed from error

    if not user.is_active:
        raise AuthenticationFailed

    return user


class JWTAuthentication(SimpleJWTAuthentication):
    """Authenticate bearer tokens against authoritative account state.

    Inherits from ``SimpleJWTAuthentication`` while replacing account lookup with an explicit
    primary read and normalizing every token failure into the project's generic error contract.

    Attributes:
        None beyond those inherited from ``JWTAuthentication``.

    Members:
        authenticate: Normalize package-specific token failures.
        get_validated_token: Normalize upstream temporal-claim type failures.
        get_user: Resolve the current active account on the primary.
    """

    @override
    def authenticate(  # type: ignore[override]
        self,
        request: Request,
    ) -> tuple[User, JWTToken] | None:
        """Authenticate one bearer credential without exposing rejection details.

        Delegates header and signature handling to SimpleJWT, then converts its detailed failures
        into the stable project exception before the central envelope renders them.

        Arguments:
            request: REST request carrying optional authorization metadata.

        Returns:
            Active account and validated access token, or None when no bearer token was supplied.

        Raises:
            AuthenticationFailed: If a bearer credential or its account is invalid.
            ServiceUnavailable: If authoritative account state cannot be read.
        """
        try:
            result = super().authenticate(request)
        except SimpleJWTAuthenticationFailed as error:
            raise AuthenticationFailed from error
        except DatabaseError as error:
            raise ServiceUnavailable from error

        if result is None:
            return None

        user, token = result

        return cast("User", user), token

    @override
    def get_validated_token(self, raw_token: bytes) -> JWTToken:
        """Validate one encoded access token without leaking upstream type failures.

        Restricts normalization to SimpleJWT's token construction and PyJWT claim validation so
        invalid temporal claim types and ranges become the same generic authentication failure as
        malformed JWT.

        Arguments:
            raw_token: Encoded bearer credential extracted from the authorization header.

        Returns:
            Validated access-token wrapper.

        Raises:
            AuthenticationFailed: If upstream decoding or claim validation rejects the token.
        """
        try:
            return super().get_validated_token(raw_token)
        except (TypeError, OverflowError, OSError) as error:
            raise SimpleJWTAuthenticationFailed from error

    @override
    def get_user(self, validated_token: JWTToken) -> User:  # type: ignore[override]
        """Resolve one token owner through the primary database.

        Delegates identity parsing and authoritative lookup to the shared protocol helper so bearer
        authentication matches refresh and verification behavior.

        Arguments:
            validated_token: Access token whose identity claim has passed signature validation.

        Returns:
            Active account named by the token.

        Raises:
            AuthenticationFailed: If identity is absent, invalid, unknown, or inactive.
            DatabaseError: If the primary cannot be read.
        """
        return active_token_user(validated_token)


class JWTAuthenticationScheme(OpenApiAuthenticationExtension):  # type: ignore[no-untyped-call]
    """Declare the primary JSON web token security scheme.

    Inherits from drf-spectacular's ``OpenApiAuthenticationExtension`` and maps the project
    primary-reading authenticator to the standard bearer-header shape.

    Attributes:
        target_class: Project JWT authenticator represented by this extension.
        name: Stable OpenAPI security scheme name.

    Members:
        get_security_definition: Build the reserved bearer scheme document.
    """

    target_class = "accounts.authentication.JWTAuthentication"
    name = "jwtAuth"

    @override
    def get_security_definition(self, auto_schema: object) -> dict[str, str]:
        """Build the JSON web token security definition.

        Emits the bearer transport and JWT format metadata clients need to present access tokens
        through the runtime authenticator.

        Arguments:
            auto_schema: Schema generator requesting the security definition.

        Returns:
            OpenAPI bearer scheme with JWT format metadata.
        """
        del auto_schema

        return {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"}


class PrimaryTokenAuthentication(TokenAuthentication):
    """Authenticate DRF tokens against the writable database.

    Inherits from DRF's ``TokenAuthentication`` but reads the token and account from the primary,
    preventing replication lag from rejecting a token immediately after login or accepting one
    immediately after logout.

    Attributes:
        None beyond those inherited from ``TokenAuthentication``.

    Members:
        authenticate_credentials: Resolve one token key on the primary.
    """

    @override
    def authenticate_credentials(self, key: str) -> tuple[User, Token]:
        """Resolve a token and active account from the primary database.

        Preserves DRF's credential checks while selecting the authoritative connection explicitly,
        which makes token creation and revocation immediately visible to the next request.

        Arguments:
            key: Token value parsed from the authorization header.

        Returns:
            Active account and persisted token.

        Raises:
            AuthenticationFailed: If the token is unknown or its account is inactive.
        """
        try:
            token = Token.objects.using("default").select_related("user").get(key=key)
        except Token.DoesNotExist as error:
            raise AuthenticationFailed from error

        user = cast("User", token.user)
        if not user.is_active:
            raise AuthenticationFailed

        return user, token


class PrimaryTokenAuthenticationScheme(  # type: ignore[no-untyped-call]
    OpenApiAuthenticationExtension
):
    """Declare the secondary DRF token security scheme.

    Inherits from drf-spectacular's ``OpenApiAuthenticationExtension`` and maps the primary-reading
    adapter to the exact runtime authorization-header syntax.

    Attributes:
        target_class: Project token authenticator represented by this extension.
        name: Stable OpenAPI security scheme name.

    Members:
        get_security_definition: Build the token header scheme document.
    """

    target_class = "accounts.authentication.PrimaryTokenAuthentication"
    name = "tokenAuth"

    @override
    def get_security_definition(self, auto_schema: object) -> dict[str, str]:
        """Build the OpenAPI definition for DRF token authentication.

        Documents the authorization header and required ``Token`` prefix so Swagger and generated
        clients can construct the same credential syntax the runtime parses.

        Arguments:
            auto_schema: Schema generator requesting the security definition.

        Returns:
            OpenAPI API-key scheme carried in the authorization header.
        """
        del auto_schema

        return {
            "type": "apiKey",
            "in": "header",
            "name": "Authorization",
            "description": "Send `Authorization: Token <key>`.",
        }
