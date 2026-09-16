"""REST authentication scheme ordering.

Provides a permanently inert JSON web token placeholder before DRF token authentication. Ticket 30
must explicitly replace and configure the placeholder together with its issuance routes.
"""

# mypy: disable-error-code=misc

from typing import TYPE_CHECKING, cast, override

from drf_spectacular.extensions import OpenApiAuthenticationExtension
from rest_framework.authentication import BaseAuthentication, TokenAuthentication
from rest_framework.authtoken.models import Token
from rest_framework.exceptions import AuthenticationFailed

if TYPE_CHECKING:
    from rest_framework.request import Request

    from accounts.models import User


class JWTAuthentication(BaseAuthentication):
    """Reserve the future JSON web token position without authenticating.

    Inherits from DRF's ``BaseAuthentication`` and ignores every request regardless of installed
    packages, ensuring dependency presence cannot activate speculative authentication behavior.

    Members:
        authenticate: Ignore all credentials until Ticket 30 replaces this class.
        authenticate_header: Advertise the reserved bearer challenge.
    """

    @override
    def authenticate(self, request: Request) -> None:
        """Ignore every credential presented to the placeholder.

        Performs no package discovery, parsing, validation, or delegation so Ticket 29 can never
        authenticate a bearer token accidentally.

        Arguments:
            request: REST request carrying optional authorization metadata.

        Returns:
            Always None.
        """
        del request

    @override
    def authenticate_header(self, request: Request) -> str:
        """Advertise the reserved bearer challenge.

        Keeps unauthorized responses at status 401 while making no claim that this placeholder can
        validate a bearer credential.

        Arguments:
            request: REST request being challenged.

        Returns:
            Stable bearer scheme name.
        """
        del request

        return "Bearer"


class JWTAuthenticationScheme(OpenApiAuthenticationExtension):  # type: ignore[no-untyped-call]
    """Declare the reserved JSON web token security scheme.

    Inherits from drf-spectacular's ``OpenApiAuthenticationExtension`` and maps the placeholder to
    its future standard header shape without implementing issuance or validation.

    Attributes:
        target_class: Project placeholder represented by this extension.
        name: Stable OpenAPI security scheme name.

    Members:
        get_security_definition: Build the reserved bearer scheme document.
    """

    target_class = "accounts.authentication.JWTAuthentication"
    name = "jwtAuth"

    @override
    def get_security_definition(self, auto_schema: object) -> dict[str, str]:
        """Build the reserved JSON web token security definition.

        Emits only transport metadata; Ticket 30 must replace the runtime authenticator explicitly
        before the scheme can authenticate.

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
