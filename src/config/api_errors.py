"""Stable REST API error vocabulary.

Defines every machine-readable error, its safe message, framework exception mapping, and governing
status registry in a dependency-light module shared by runtime handlers and endpoint schema
annotations.
"""

# mypy: disable-error-code=misc

from dataclasses import dataclass
from enum import StrEnum
from http import HTTPStatus
from types import MappingProxyType

from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.http import Http404
from rest_framework.exceptions import (
    APIException,
    AuthenticationFailed,
    MethodNotAllowed,
    NotAcceptable,
    NotAuthenticated,
    NotFound,
    ParseError,
    PermissionDenied,
    Throttled,
    UnsupportedMediaType,
    ValidationError,
)


class ErrorCode(StrEnum):
    """Enumerate every machine-readable HTTP API error code.

    Inherits from ``StrEnum`` so codes serialize as JSON strings while remaining impossible to
    invent as unchecked literals at response call sites.

    Attributes:
        API_ERROR: Framework failure without a more specific public category.
        AUTHENTICATION_FAILED: Credentials were supplied but rejected.
        BAD_REQUEST: Request rejection outside REST framework parsing.
        REQUEST_TOO_LARGE: Request body exceeds the configured API limit.
        INTERNAL_SERVER_ERROR: Sanitized unexpected server failure.
        METHOD_NOT_ALLOWED: HTTP method is unsupported for the route.
        NOT_ACCEPTABLE: Requested response representation is unavailable.
        NOT_AUTHENTICATED: Required credentials were not supplied.
        NOT_FOUND: Route or resource was not found.
        PARSE_ERROR: Request body could not be parsed.
        PERMISSION_DENIED: Caller may not perform the operation.
        SERVICE_UNAVAILABLE: Required service is temporarily unavailable.
        THROTTLED: Request exceeded an applicable rate limit.
        UNSUPPORTED_MEDIA_TYPE: Request representation is unsupported.
        VALIDATION_ERROR: Submitted fields failed validation.
        ACTIVATION_TOKEN_EXPIRED: Activation token exceeded its configured lifetime.
        ACTIVATION_TOKEN_FOREIGN: Activation token does not belong to the submitted account.
        ACTIVATION_TOKEN_MALFORMED: Activation token cannot be decoded or authenticated.
        ACTIVATION_TOKEN_USED: Activation token has already completed activation.

    Members:
        None beyond those inherited from ``StrEnum``.
    """

    API_ERROR = "api_error"
    AUTHENTICATION_FAILED = "authentication_failed"
    BAD_REQUEST = "bad_request"
    REQUEST_TOO_LARGE = "request_too_large"
    INTERNAL_SERVER_ERROR = "internal_server_error"
    METHOD_NOT_ALLOWED = "method_not_allowed"
    NOT_ACCEPTABLE = "not_acceptable"
    NOT_AUTHENTICATED = "not_authenticated"
    NOT_FOUND = "not_found"
    PARSE_ERROR = "parse_error"
    PERMISSION_DENIED = "permission_denied"
    SERVICE_UNAVAILABLE = "service_unavailable"
    THROTTLED = "throttled"
    UNSUPPORTED_MEDIA_TYPE = "unsupported_media_type"
    VALIDATION_ERROR = "validation_error"
    ACTIVATION_TOKEN_EXPIRED = "activation_token_expired"  # noqa: S105
    ACTIVATION_TOKEN_FOREIGN = "activation_token_foreign"  # noqa: S105
    ACTIVATION_TOKEN_MALFORMED = "activation_token_malformed"  # noqa: S105
    ACTIVATION_TOKEN_USED = "activation_token_used"  # noqa: S105


class ServiceUnavailable(APIException):
    """Represent loss of a required dependency at an API boundary.

    Inherits from DRF's ``APIException`` and fixes the status while the central handler supplies
    the stable public code and message.

    Attributes:
        status_code: HTTP service-unavailable status.
        default_code: Stable framework-facing exception code.
        default_detail: Safe framework-facing exception detail.

    Members:
        None.
    """

    status_code = HTTPStatus.SERVICE_UNAVAILABLE
    default_code = ErrorCode.SERVICE_UNAVAILABLE
    default_detail = "A required service is unavailable."


class ActivationTokenExpired(APIException):
    """Represent an activation token beyond its allowed lifetime.

    Inherits from DRF's ``APIException`` and fixes the public status while the shared handler
    supplies the stable code and message.

    Attributes:
        status_code: HTTP bad-request status.
        default_code: Stable framework-facing exception code.
        default_detail: Safe framework-facing exception detail.

    Members:
        None.
    """

    status_code = HTTPStatus.BAD_REQUEST
    default_code = ErrorCode.ACTIVATION_TOKEN_EXPIRED
    default_detail = "The activation token has expired."


class ActivationTokenForeign(APIException):
    """Represent a token not bound to the submitted account.

    Inherits from DRF's ``APIException`` and deliberately covers missing accounts and missing token
    records too, preventing the distinction from disclosing account existence.

    Attributes:
        status_code: HTTP bad-request status.
        default_code: Stable framework-facing exception code.
        default_detail: Safe framework-facing exception detail.

    Members:
        None.
    """

    status_code = HTTPStatus.BAD_REQUEST
    default_code = ErrorCode.ACTIVATION_TOKEN_FOREIGN
    default_detail = "The activation token does not match the account."


class ActivationTokenMalformed(APIException):
    """Represent a token that cannot be authenticated or decoded.

    Inherits from DRF's ``APIException`` and exposes no signing or parsing detail through the
    shared error envelope.

    Attributes:
        status_code: HTTP bad-request status.
        default_code: Stable framework-facing exception code.
        default_detail: Safe framework-facing exception detail.

    Members:
        None.
    """

    status_code = HTTPStatus.BAD_REQUEST
    default_code = ErrorCode.ACTIVATION_TOKEN_MALFORMED
    default_detail = "The activation token is malformed."


class ActivationTokenUsed(APIException):
    """Represent an activation token consumed by an earlier confirmation.

    Inherits from DRF's ``APIException`` and distinguishes replay from malformed or expired input
    without exposing any account fields.

    Attributes:
        status_code: HTTP bad-request status.
        default_code: Stable framework-facing exception code.
        default_detail: Safe framework-facing exception detail.

    Members:
        None.
    """

    status_code = HTTPStatus.BAD_REQUEST
    default_code = ErrorCode.ACTIVATION_TOKEN_USED
    default_detail = "The activation token has already been used."


@dataclass(frozen=True, slots=True)
class ErrorDefinition:
    """Pair a stable error code with its safe human-readable message.

    Carries the two client-facing values that must change together and prevents exception handlers
    from deriving public text from implementation-specific exception details.

    Attributes:
        code: Stable machine-readable error code.
        message: Safe human-readable explanation.

    Members:
        None.
    """

    code: ErrorCode
    message: str


API_ERROR = ErrorDefinition(
    ErrorCode.API_ERROR,
    "The request could not be completed.",
)
AUTHENTICATION_FAILED = ErrorDefinition(
    ErrorCode.AUTHENTICATION_FAILED,
    "Authentication failed.",
)
BAD_REQUEST = ErrorDefinition(
    ErrorCode.BAD_REQUEST,
    "The request was invalid.",
)
REQUEST_TOO_LARGE = ErrorDefinition(
    ErrorCode.REQUEST_TOO_LARGE,
    "The request body is too large.",
)
INTERNAL_SERVER_ERROR = ErrorDefinition(
    ErrorCode.INTERNAL_SERVER_ERROR,
    "An unexpected error occurred.",
)
METHOD_NOT_ALLOWED = ErrorDefinition(
    ErrorCode.METHOD_NOT_ALLOWED,
    "The requested method is not allowed.",
)
NOT_ACCEPTABLE = ErrorDefinition(
    ErrorCode.NOT_ACCEPTABLE,
    "The requested response format is not available.",
)
NOT_AUTHENTICATED = ErrorDefinition(
    ErrorCode.NOT_AUTHENTICATED,
    "Authentication credentials were not provided.",
)
NOT_FOUND = ErrorDefinition(
    ErrorCode.NOT_FOUND,
    "The requested resource was not found.",
)
PARSE_ERROR = ErrorDefinition(
    ErrorCode.PARSE_ERROR,
    "The request body could not be parsed.",
)
PERMISSION_DENIED = ErrorDefinition(
    ErrorCode.PERMISSION_DENIED,
    "You do not have permission to perform this action.",
)
SERVICE_UNAVAILABLE = ErrorDefinition(
    ErrorCode.SERVICE_UNAVAILABLE,
    "A required service is unavailable.",
)
THROTTLED = ErrorDefinition(
    ErrorCode.THROTTLED,
    "Too many requests.",
)
UNSUPPORTED_MEDIA_TYPE = ErrorDefinition(
    ErrorCode.UNSUPPORTED_MEDIA_TYPE,
    "The request content type is not supported.",
)
VALIDATION_ERROR = ErrorDefinition(
    ErrorCode.VALIDATION_ERROR,
    "The submitted data is invalid.",
)
ACTIVATION_TOKEN_EXPIRED = ErrorDefinition(
    ErrorCode.ACTIVATION_TOKEN_EXPIRED,
    "The activation token has expired.",
)
ACTIVATION_TOKEN_FOREIGN = ErrorDefinition(
    ErrorCode.ACTIVATION_TOKEN_FOREIGN,
    "The activation token does not match the account.",
)
ACTIVATION_TOKEN_MALFORMED = ErrorDefinition(
    ErrorCode.ACTIVATION_TOKEN_MALFORMED,
    "The activation token is malformed.",
)
ACTIVATION_TOKEN_USED = ErrorDefinition(
    ErrorCode.ACTIVATION_TOKEN_USED,
    "The activation token has already been used.",
)

STATUS_DEFINITIONS: dict[int, ErrorDefinition] = {
    HTTPStatus.BAD_REQUEST: BAD_REQUEST,
    HTTPStatus.UNAUTHORIZED: NOT_AUTHENTICATED,
    HTTPStatus.FORBIDDEN: PERMISSION_DENIED,
    HTTPStatus.NOT_FOUND: NOT_FOUND,
    HTTPStatus.METHOD_NOT_ALLOWED: METHOD_NOT_ALLOWED,
    HTTPStatus.NOT_ACCEPTABLE: NOT_ACCEPTABLE,
    HTTPStatus.CONTENT_TOO_LARGE: REQUEST_TOO_LARGE,
    HTTPStatus.UNSUPPORTED_MEDIA_TYPE: UNSUPPORTED_MEDIA_TYPE,
    HTTPStatus.TOO_MANY_REQUESTS: THROTTLED,
    HTTPStatus.INTERNAL_SERVER_ERROR: INTERNAL_SERVER_ERROR,
    HTTPStatus.SERVICE_UNAVAILABLE: SERVICE_UNAVAILABLE,
}

ERROR_STATUS_REGISTRY = MappingProxyType(
    {
        HTTPStatus.BAD_REQUEST: frozenset(
            {
                ErrorCode.BAD_REQUEST,
                ErrorCode.ACTIVATION_TOKEN_EXPIRED,
                ErrorCode.ACTIVATION_TOKEN_FOREIGN,
                ErrorCode.ACTIVATION_TOKEN_MALFORMED,
                ErrorCode.ACTIVATION_TOKEN_USED,
                ErrorCode.PARSE_ERROR,
                ErrorCode.VALIDATION_ERROR,
            }
        ),
        HTTPStatus.UNAUTHORIZED: frozenset(
            {
                ErrorCode.AUTHENTICATION_FAILED,
                ErrorCode.NOT_AUTHENTICATED,
            }
        ),
        HTTPStatus.FORBIDDEN: frozenset(
            {
                ErrorCode.NOT_AUTHENTICATED,
                ErrorCode.PERMISSION_DENIED,
            }
        ),
        HTTPStatus.NOT_FOUND: frozenset({ErrorCode.NOT_FOUND}),
        HTTPStatus.METHOD_NOT_ALLOWED: frozenset({ErrorCode.METHOD_NOT_ALLOWED}),
        HTTPStatus.NOT_ACCEPTABLE: frozenset({ErrorCode.NOT_ACCEPTABLE}),
        HTTPStatus.CONTENT_TOO_LARGE: frozenset({ErrorCode.REQUEST_TOO_LARGE}),
        HTTPStatus.UNSUPPORTED_MEDIA_TYPE: frozenset({ErrorCode.UNSUPPORTED_MEDIA_TYPE}),
        HTTPStatus.TOO_MANY_REQUESTS: frozenset({ErrorCode.THROTTLED}),
        HTTPStatus.INTERNAL_SERVER_ERROR: frozenset(
            {
                ErrorCode.API_ERROR,
                ErrorCode.INTERNAL_SERVER_ERROR,
            }
        ),
        HTTPStatus.SERVICE_UNAVAILABLE: frozenset({ErrorCode.SERVICE_UNAVAILABLE}),
    }
)

EXCEPTION_DEFINITIONS: dict[type[Exception], ErrorDefinition] = {
    ValidationError: VALIDATION_ERROR,
    ParseError: PARSE_ERROR,
    AuthenticationFailed: AUTHENTICATION_FAILED,
    NotAuthenticated: NOT_AUTHENTICATED,
    PermissionDenied: PERMISSION_DENIED,
    DjangoPermissionDenied: PERMISSION_DENIED,
    NotFound: NOT_FOUND,
    Http404: NOT_FOUND,
    MethodNotAllowed: METHOD_NOT_ALLOWED,
    NotAcceptable: NOT_ACCEPTABLE,
    UnsupportedMediaType: UNSUPPORTED_MEDIA_TYPE,
    Throttled: THROTTLED,
    ServiceUnavailable: SERVICE_UNAVAILABLE,
    ActivationTokenExpired: ACTIVATION_TOKEN_EXPIRED,
    ActivationTokenForeign: ACTIVATION_TOKEN_FOREIGN,
    ActivationTokenMalformed: ACTIVATION_TOKEN_MALFORMED,
    ActivationTokenUsed: ACTIVATION_TOKEN_USED,
    APIException: API_ERROR,
}
