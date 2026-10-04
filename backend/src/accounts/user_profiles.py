"""User registration and self-service profile endpoints.

Creates inactive accounts without identifier disclosure and exposes only the authenticated caller's
profile, keeping account discovery and privileged state outside the fixed versioned API surface.
"""

# mypy: disable-error-code=misc

import secrets
from collections.abc import Mapping
from hashlib import sha256
from http import HTTPStatus
from typing import TYPE_CHECKING, Any, cast, override

from django.conf import settings
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import DatabaseError, IntegrityError, transaction
from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiResponse,
    PolymorphicProxySerializer,
    extend_schema,
)
from rest_framework.exceptions import AuthenticationFailed, ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.serializers import CharField, EmailField, Serializer, UUIDField
from rest_framework.throttling import BaseThrottle
from rest_framework.views import APIView

import accounts.credentials as credential_policy
from accounts.account_activation import (
    RESEND_ACCEPTED_MESSAGE,
    ActivationConfirmationSerializer,
    ActivationResendSerializer,
    ActivationResendThrottle,
    admit_duplicate_activation,
    confirm_activation,
    issue_activation,
    request_activation_resend,
    schedule_dummy_activation,
    validated_activation_resend_data,
)
from accounts.api_throttling import (
    AnonymousApiThrottle,
    AuthenticatedReadThrottle,
    AuthenticationRecoveryThrottle,
    AuthenticationRecoveryWriteThrottle,
)
from accounts.login_throttle import PostgresLoginThrottleStore, RollingWindowRule
from accounts.models import User
from accounts.request_throttling import parse_throttle_rate, trusted_client_address
from accounts.request_validation import StrictRequestSerializer, normalise_valid_email
from accounts.response_timing import (
    monotonic_now,
    wait_for_minimum_response_duration,
)
from accounts.token_authentication import (
    ErrorEnvelopeSerializer,
    error_example,
    error_response,
)
from config.api_errors import (
    ACTIVATION_TOKEN_EXPIRED,
    ACTIVATION_TOKEN_FOREIGN,
    ACTIVATION_TOKEN_MALFORMED,
    ACTIVATION_TOKEN_USED,
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
    ServiceUnavailable,
)
from config.logs import REQUEST_ID_META_KEY

if TYPE_CHECKING:
    from rest_framework.request import Request


class RegistrationSerializer(StrictRequestSerializer):
    """Validate one public account registration.

    Inherits from ``StrictRequestSerializer`` and checks the submitted password against every
    configured validator using the candidate identifiers, while keeping both password fields
    write-only.

    Attributes:
        username: Public account name.
        email: Public account address.
        password: Raw credential accepted only as input.
        password_confirm: Repeated raw credential accepted only as input.

    Members:
        validate_email: Canonicalize the address before validation completes.
        validate: Enforce confirmation and configured password policy.
    """

    username = CharField(max_length=150)
    email = EmailField(max_length=254)
    password = CharField(trim_whitespace=False, write_only=True)
    password_confirm = CharField(trim_whitespace=False, write_only=True)

    def validate_email(self, value: str) -> str:
        """Canonicalize the submitted address.

        Applies the model's storage normalization before the public response is built, ensuring the
        accepted representation and the persisted identifier cannot disagree.

        Arguments:
            value: Valid syntactic email address.

        Returns:
            Lowercased and trimmed email address.
        """
        return normalise_valid_email(value)

    @override
    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        """Enforce password confirmation and every configured validator.

        Supplies the candidate account to Django's password policy so similarity checks can use the
        same username and email that a successful request persists.

        Arguments:
            attrs: Individually validated registration fields.

        Returns:
            Validated registration fields.

        Raises:
            ValidationError: If confirmation differs or password policy rejects the credential.
        """
        password = cast("str", attrs["password"])
        errors: dict[str, list[str]] = {}
        if password != attrs["password_confirm"]:
            errors["password_confirm"] = ["The password confirmation does not match."]

        candidate = User(
            username=cast("str", attrs["username"]),
            email=cast("str", attrs["email"]),
        )
        try:
            validate_password(password, user=candidate)
        except DjangoValidationError as error:
            errors["password"] = list(error.messages)

        if errors:
            raise ValidationError(errors)

        return attrs


class RegistrationResponseSerializer(Serializer):
    """Describe the enumeration-resistant registration response.

    Inherits from DRF's ``Serializer`` and exposes only the accepted username and normalized email,
    omitting database identity and account state so a duplicate request can return the same body.

    Attributes:
        username: Submitted public account name.
        email: Submitted normalized email address.

    Members:
        None beyond those inherited from ``Serializer``.
    """

    username = CharField(read_only=True)
    email = EmailField(read_only=True)


class ActivationResendResponseSerializer(Serializer):
    """Describe the enumeration-resistant resend response.

    Inherits from DRF's ``Serializer`` and exposes one accepted statement shared by unknown,
    inactive, and active account outcomes.

    Attributes:
        detail: State-independent accepted response text.

    Members:
        None beyond those inherited from ``Serializer``.
    """

    detail = CharField(read_only=True)


class UserProfileSerializer(Serializer):
    """Describe the authenticated caller's public profile.

    Inherits from DRF's ``Serializer`` and exposes the immutable key and two public identifiers,
    omitting activation, permission, password, token, and operational timestamp state.

    Attributes:
        id: Immutable account identifier.
        username: Public account name changed only through its dedicated route.
        email: Public address and the sole mutable profile field.

    Members:
        None beyond those inherited from ``Serializer``.
    """

    id = UUIDField(read_only=True)
    username = CharField(read_only=True)
    email = EmailField(read_only=True)


class UserProfileUpdateSerializer(StrictRequestSerializer):
    """Validate the sole mutable profile field.

    Inherits from ``StrictRequestSerializer`` and accepts only email, rejecting identifiers,
    credentials, activation state, and permissions instead of silently ignoring them.

    Attributes:
        email: Replacement public address.

    Members:
        validate_email: Canonicalize the replacement address.
    """

    email = EmailField(max_length=254, required=False)

    def validate_email(self, value: str) -> str:
        """Canonicalize the replacement address.

        Applies the account model's storage normalization before PostgreSQL evaluates uniqueness,
        keeping the returned representation identical to persisted state.

        Arguments:
            value: Valid syntactic replacement email address.

        Returns:
            Lowercased and trimmed email address.
        """
        return normalise_valid_email(value)


class UserProfileDeletionSerializer(StrictRequestSerializer):
    """Validate the credential required to delete the caller's profile.

    Inherits from ``StrictRequestSerializer`` and accepts only the current password, preventing
    deletion requests from carrying an account selector or unrelated mutable state.

    Attributes:
        current_password: Raw current credential accepted only as input.

    Members:
        None beyond those inherited from ``Serializer``.
    """

    current_password = CharField(trim_whitespace=False, write_only=True)


class UserRegistrationThrottle(BaseThrottle):
    """Enforce one authoritative registration limit per client address.

    Inherits from DRF's ``BaseThrottle`` and writes admission to PostgreSQL beside account state,
    so cache eviction, cache outage, and multiple application processes cannot reset the limit.

    Attributes:
        retry_after_seconds: Authoritative delay after a rejected request.

    Members:
        allow_request: Atomically admit or reject one registration address.
        wait: Return the retry delay for DRF's response header.
    """

    retry_after_seconds: int | None = None

    @override
    def allow_request(self, request: Request, view: APIView) -> bool:
        """Atomically enforce the configured registration address window.

        Admits metadata and unsupported methods without recording them, then hashes a POST caller's
        address and fails closed if PostgreSQL cannot make the admission decision.

        Arguments:
            request: Registration request being admitted.
            view: Registration view applying the throttle.

        Returns:
            Whether authoritative shared state admitted the request.

        Raises:
            ServiceUnavailable: If authoritative throttle state is unavailable.
            ValueError: If the configured rate is invalid.
        """
        del view
        if request.method != "POST" or (
            isinstance(request.data, Mapping) and set(request.data) == {"account", "token"}
        ):
            return True

        limit, window_seconds = parse_throttle_rate(
            cast("str", settings.USER_REGISTRATION_ADDRESS_THROTTLE_RATE)
        )
        address = sha256(trusted_client_address(request).encode()).hexdigest()
        request_id = request.META.get(REQUEST_ID_META_KEY)
        correlation = request_id if isinstance(request_id, str) else "uncorrelated"
        member = f"{correlation}:{secrets.token_hex(16)}"

        try:
            decision = PostgresLoginThrottleStore(settings.LOGIN_THROTTLE_DATABASE_ALIAS).admit(
                (
                    RollingWindowRule(
                        key=f"registration-address:{address}",
                        limit=limit,
                        window_seconds=window_seconds,
                    ),
                ),
                member=member,
            )
        except DatabaseError as error:
            raise ServiceUnavailable from error

        self.retry_after_seconds = decision.retry_after_seconds

        return decision.admitted

    @override
    def wait(self) -> float | None:
        """Return the authoritative registration retry delay.

        Supplies DRF with the exact primary-derived duration for ``Retry-After`` and returns no
        estimate before a denied decision has populated the value.

        Arguments:
            None.

        Returns:
            Retry delay in seconds, or None before a rejection.
        """
        return float(self.retry_after_seconds) if self.retry_after_seconds is not None else None


def register_account(validated_data: Mapping[str, object]) -> None:
    """Attempt one inactive account creation without disclosing duplicates.

    Hashes the credential before persistence and contains activation-token failure behind the same
    accepted outcome for new and duplicate candidates, rolling back a new account with its token.

    Arguments:
        validated_data: Validated registration fields.

    Returns:
        None whether the account was created or already existed.

    Raises:
        ServiceUnavailable: If authoritative account persistence or duplicate lookup fails.
    """
    account = User(
        username=cast("str", validated_data["username"]),
        email=cast("str", validated_data["email"]),
    )
    account.set_password(cast("str", validated_data["password"]))

    try:
        with transaction.atomic(using="default"):
            try:
                activation_issued = True
                with transaction.atomic(using="default"):
                    account.save(using="default", force_insert=True)
                    try:
                        issue_activation(account)
                    except DatabaseError, ServiceUnavailable:
                        activation_issued = False
                        transaction.set_rollback(True, using="default")
            except IntegrityError:
                try:
                    existing = (
                        User.objects.using("default").select_for_update().get(email=account.email)
                    )
                except User.DoesNotExist:
                    schedule_dummy_activation()
                    return
                try:
                    admitted = admit_duplicate_activation(existing, account.email)
                except DatabaseError:
                    admitted = False
                if admitted and not existing.is_active:
                    try:
                        with transaction.atomic(using="default"):
                            issue_activation(existing)
                    except DatabaseError, ServiceUnavailable:
                        schedule_dummy_activation()
                else:
                    schedule_dummy_activation()
                return

            if not activation_issued:
                schedule_dummy_activation()
    except DatabaseError as error:
        raise ServiceUnavailable from error


class UserRegistrationView(APIView):
    """Create an inactive account through an enumeration-resistant boundary.

    Inherits from DRF's ``APIView`` and opens only registration, returning accepted public
    identifiers whether PostgreSQL created the row or rejected a case-insensitive duplicate.

    Attributes:
        authentication_classes: Empty because a new caller has no credential.
        permission_classes: Public access required for registration.
        throttle_classes: Authoritative registration admission plus shared anonymous and
            authentication scopes.

    Members:
        initial: Capture the earliest practical monotonic request boundary.
        post: Validate and attempt one inactive account creation.
    """

    authentication_classes: tuple[type, ...] = ()
    permission_classes = (AllowAny,)
    throttle_classes = (
        UserRegistrationThrottle,
        AnonymousApiThrottle,
        AuthenticationRecoveryThrottle,
    )
    _registration_started_at: float

    @override
    def initial(self, request: Request, *args: Any, **kwargs: Any) -> None:
        """Capture timing before authentication, negotiation, and throttling.

        Starts the accepted-registration floor at the earliest DRF view boundary while allowing
        every validation, throttle, activation-confirmation, and infrastructure error to return
        without an artificial delay.

        Arguments:
            request: Initialized DRF request entering policy checks.
            *args: Positional route arguments supplied by dispatch.
            **kwargs: Named route arguments supplied by dispatch.

        Returns:
            None.

        Raises:
            APIException: If negotiation, permissions, or throttling rejects the request.
        """
        self._registration_started_at = monotonic_now()
        super().initial(request, *args, **kwargs)

    @extend_schema(
        operation_id="user_registration_or_activation",
        summary="Register or activate an account",
        description=(
            "Accepts exactly one of two request branches. A registration body contains username, "
            "email, password, and password_confirm and returns 201 with the submitted public "
            "identifiers. An activation body contains account and token and returns bodyless 204 "
            "after consuming a valid token. A username or email already occupied under PostgreSQL "
            "LOWER identity semantics returns the same status and body as creation, always 201, so "
            "account existence is disclosed only by the later activation email. Every completed "
            "201 response observes the environment-configured monotonic minimum duration."
        ),
        request=PolymorphicProxySerializer(
            component_name="UserRegistrationOrActivation",
            serializers=[RegistrationSerializer, ActivationConfirmationSerializer],
            resource_type_field_name=None,
        ),
        auth=[],
        responses={
            HTTPStatus.CREATED: OpenApiResponse(
                response=RegistrationResponseSerializer,
                description=(
                    "The registration was accepted; the account is inactive until activation."
                ),
                examples=[
                    OpenApiExample(
                        "Registration accepted",
                        value={
                            "username": "river",
                            "email": "river@localforge.invalid",
                        },
                        response_only=True,
                    )
                ],
            ),
            HTTPStatus.NO_CONTENT: OpenApiResponse(
                response=None,
                description=(
                    "The submitted account-bound token was valid, unused, and consumed while the "
                    "account became active."
                ),
            ),
            HTTPStatus.BAD_REQUEST: OpenApiResponse(
                response=ErrorEnvelopeSerializer,
                description=(
                    "The body is malformed, misses a required field, carries an undeclared field, "
                    "has mismatched confirmation, or fails email or password validation."
                ),
                examples=[
                    error_example("Malformed JSON", PARSE_ERROR),
                    error_example(
                        "Missing fields",
                        VALIDATION_ERROR,
                        details={
                            "username": ["This field is required."],
                            "email": ["This field is required."],
                            "password": ["This field is required."],
                            "password_confirm": ["This field is required."],
                        },
                    ),
                    error_example(
                        "Password confirmation mismatch",
                        VALIDATION_ERROR,
                        details={"password_confirm": ["The password confirmation does not match."]},
                    ),
                    error_example(
                        "Password policy failure",
                        VALIDATION_ERROR,
                        details={
                            "password": [
                                "This password is too short. It must contain at least 8 characters."
                            ]
                        },
                    ),
                    error_example(
                        "Activation token expired",
                        ACTIVATION_TOKEN_EXPIRED,
                    ),
                    error_example(
                        "Activation token belongs to another account",
                        ACTIVATION_TOKEN_FOREIGN,
                    ),
                    error_example(
                        "Activation token malformed",
                        ACTIVATION_TOKEN_MALFORMED,
                    ),
                    error_example(
                        "Activation token already used",
                        ACTIVATION_TOKEN_USED,
                    ),
                ],
            ),
            HTTPStatus.METHOD_NOT_ALLOWED: error_response(
                "The user collection accepts only POST registration or activation.",
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
                "The client address exceeded its configured registration rate.",
                "Too many requests",
                THROTTLED,
            ),
            HTTPStatus.INTERNAL_SERVER_ERROR: error_response(
                "An unexpected server failure was contained.",
                "Internal server error",
                INTERNAL_SERVER_ERROR,
            ),
            HTTPStatus.SERVICE_UNAVAILABLE: error_response(
                "Authoritative account persistence or registration-admission state is unavailable.",
                "Service unavailable",
                SERVICE_UNAVAILABLE,
            ),
        },
    )
    def post(self, request: Request) -> Response:
        """Validate and attempt one inactive account creation.

        Applies confirmation and password policy before persistence, then returns only the accepted
        public identifiers so success and duplicate outcomes remain indistinguishable.

        Arguments:
            request: REST request carrying registration fields.

        Returns:
            Accepted public registration representation.

        Raises:
            ServiceUnavailable: If authoritative persistence is unavailable.
            ValidationError: If any submitted field fails validation.
        """
        if isinstance(request.data, Mapping) and set(request.data) == {"account", "token"}:
            activation = ActivationConfirmationSerializer(data=request.data)
            activation.is_valid(raise_exception=True)
            confirm_activation(activation.validated_data)
            return Response(status=HTTPStatus.NO_CONTENT)

        serializer = RegistrationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        register_account(serializer.validated_data)
        wait_for_minimum_response_duration(
            self._registration_started_at,
            settings.USER_REGISTRATION_MINIMUM_RESPONSE_DURATION_SECONDS,
        )
        return Response(
            {
                "username": serializer.validated_data["username"],
                "email": serializer.validated_data["email"],
            },
            status=HTTPStatus.CREATED,
        )


class ActivationResendView(APIView):
    """Accept an activation resend without exposing account state.

    Inherits from DRF's ``APIView`` and returns one accepted representation for unknown, inactive,
    and active addresses while authoritative task execution sends only for an inactive account.

    Attributes:
        authentication_classes: Empty because an inactive caller has no credential.
        permission_classes: Public access required for activation recovery.
        throttle_classes: Authoritative resend admission plus shared anonymous and authentication
            scopes.

    Members:
        post: Validate and schedule one indistinguishable resend outcome.
    """

    authentication_classes: tuple[type, ...] = ()
    permission_classes = (AllowAny,)
    throttle_classes = (
        ActivationResendThrottle,
        AnonymousApiThrottle,
        AuthenticationRecoveryThrottle,
    )

    @extend_schema(
        operation_id="user_activation_resend",
        summary="Resend account activation",
        description=(
            "Accepts an email address and returns the same response for unknown, inactive, and "
            "active accounts. Only an inactive matching account receives a fresh activation link."
        ),
        request=ActivationResendSerializer,
        auth=[],
        responses={
            HTTPStatus.ACCEPTED: OpenApiResponse(
                response=ActivationResendResponseSerializer,
                description=(
                    "The resend request was accepted without disclosing whether an account matched."
                ),
                examples=[
                    OpenApiExample(
                        "Activation resend accepted",
                        value={"detail": RESEND_ACCEPTED_MESSAGE},
                        response_only=True,
                    )
                ],
            ),
            HTTPStatus.BAD_REQUEST: OpenApiResponse(
                response=ErrorEnvelopeSerializer,
                description=(
                    "The body is malformed, misses the email field, carries an undeclared field, "
                    "or contains an invalid email address."
                ),
                examples=[
                    error_example("Malformed JSON", PARSE_ERROR),
                    error_example(
                        "Invalid email",
                        VALIDATION_ERROR,
                        details={"email": ["Enter a valid email address."]},
                    ),
                ],
            ),
            HTTPStatus.METHOD_NOT_ALLOWED: error_response(
                "Activation resend accepts only POST.",
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
                "The client address or account exceeded its configured activation resend rate.",
                "Too many requests",
                THROTTLED,
            ),
            HTTPStatus.INTERNAL_SERVER_ERROR: error_response(
                "An unexpected server failure was contained.",
                "Internal server error",
                INTERNAL_SERVER_ERROR,
            ),
            HTTPStatus.SERVICE_UNAVAILABLE: error_response(
                "Authoritative account, token, or resend-admission state is unavailable.",
                "Service unavailable",
                SERVICE_UNAVAILABLE,
            ),
        },
    )
    def post(self, request: Request) -> Response:
        """Validate and accept one activation resend.

        Normalizes the address before account resolution and returns the same body whether no
        account, an inactive account, or an active account matched.

        Arguments:
            request: REST request carrying one email address.

        Returns:
            Enumeration-resistant accepted representation.

        Raises:
            ServiceUnavailable: If authoritative persistence is unavailable.
            ValidationError: If the submitted address fails validation.
        """
        validated_data = validated_activation_resend_data(request)
        request_activation_resend(cast("str", validated_data["email"]))
        return Response(
            {"detail": RESEND_ACCEPTED_MESSAGE},
            status=HTTPStatus.ACCEPTED,
        )


class UserProfileView(APIView):
    """Read, update, or delete only the authenticated caller's profile.

    Inherits from DRF's ``APIView`` and obtains account identity exclusively from authentication,
    leaving no path or body selector that can address another account.

    Attributes:
        permission_classes: Authenticated callers only.
        throttle_classes: Authenticated-read scope for GET and authentication scope for mutations.

    Members:
        get: Return the caller's public profile.
        patch: Update only the caller's email address.
        delete: Irreversibly remove the caller after password confirmation.
    """

    permission_classes = (IsAuthenticated,)
    throttle_classes = (
        AuthenticatedReadThrottle,
        AuthenticationRecoveryWriteThrottle,
    )

    @extend_schema(
        operation_id="user_profile_retrieve",
        summary="Return the caller's profile",
        description=(
            "Returns only the account resolved from the supplied credential. Query parameters are "
            "ignored and no path or request identifier can select another account."
        ),
        request=None,
        responses={
            HTTPStatus.OK: OpenApiResponse(
                response=UserProfileSerializer,
                description="The authenticated caller's public profile.",
                examples=[
                    OpenApiExample(
                        "Caller profile",
                        value={
                            "id": "00000000-0000-7000-8000-000000000000",
                            "username": "river",
                            "email": "river@localforge.invalid",
                        },
                        response_only=True,
                    )
                ],
            ),
            HTTPStatus.UNAUTHORIZED: OpenApiResponse(
                response=ErrorEnvelopeSerializer,
                description="A credential was absent, malformed, expired, revoked, or inactive.",
                examples=[
                    error_example("Credential absent", NOT_AUTHENTICATED),
                    error_example("Authentication failed", AUTHENTICATION_FAILED),
                ],
            ),
            HTTPStatus.METHOD_NOT_ALLOWED: error_response(
                "The self-profile route supports only GET, PATCH, and DELETE.",
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
                "The client address or account exceeded the authenticated-read scope.",
                "Too many requests",
                THROTTLED,
            ),
            HTTPStatus.INTERNAL_SERVER_ERROR: error_response(
                "An unexpected server failure was contained.",
                "Internal server error",
                INTERNAL_SERVER_ERROR,
            ),
            HTTPStatus.SERVICE_UNAVAILABLE: error_response(
                "Authoritative account or credential state is unavailable.",
                "Service unavailable",
                SERVICE_UNAVAILABLE,
            ),
        },
    )
    def get(self, request: Request) -> Response:
        """Return the authenticated caller's public profile.

        Serializes only the account already resolved by authentication and deliberately ignores
        query parameters, so no submitted identifier can select another row.

        Arguments:
            request: Authenticated REST request.

        Returns:
            Caller profile containing id, username, and email.
        """
        account = cast("User", request.user)

        return Response(UserProfileSerializer(account).data, status=HTTPStatus.OK)

    @extend_schema(
        operation_id="user_profile_update",
        summary="Update the caller's mutable profile",
        description=(
            "Partially updates only email. The account id, username, password, activation flag, "
            "staff and superuser flags, groups, and permissions are rejected as undeclared fields."
        ),
        request=UserProfileUpdateSerializer,
        responses={
            HTTPStatus.OK: OpenApiResponse(
                response=UserProfileSerializer,
                description="The caller's profile after applying the submitted email change.",
                examples=[
                    OpenApiExample(
                        "Profile updated",
                        value={
                            "id": "00000000-0000-7000-8000-000000000000",
                            "username": "river",
                            "email": "updated@localforge.invalid",
                        },
                        response_only=True,
                    )
                ],
            ),
            HTTPStatus.BAD_REQUEST: OpenApiResponse(
                response=ErrorEnvelopeSerializer,
                description=(
                    "The body is malformed, email is invalid or occupied, or a forbidden field "
                    "was submitted."
                ),
                examples=[
                    error_example("Malformed JSON", PARSE_ERROR),
                    error_example(
                        "Invalid email",
                        VALIDATION_ERROR,
                        details={"email": ["Enter a valid email address."]},
                    ),
                    error_example(
                        "Forbidden field",
                        VALIDATION_ERROR,
                        details={"is_active": ["This field is not allowed."]},
                    ),
                ],
            ),
            HTTPStatus.UNAUTHORIZED: OpenApiResponse(
                response=ErrorEnvelopeSerializer,
                description="A credential was absent, malformed, expired, revoked, or inactive.",
                examples=[
                    error_example("Credential absent", NOT_AUTHENTICATED),
                    error_example("Authentication failed", AUTHENTICATION_FAILED),
                ],
            ),
            HTTPStatus.METHOD_NOT_ALLOWED: error_response(
                "The self-profile route supports only GET, PATCH, and DELETE.",
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
                "Authoritative account or credential state is unavailable.",
                "Service unavailable",
                SERVICE_UNAVAILABLE,
            ),
        },
    )
    def patch(self, request: Request) -> Response:
        """Update only the authenticated caller's email address.

        Rejects every undeclared field, normalizes an accepted address, and lets PostgreSQL's
        case-insensitive constraint authoritatively reject a conflict as field validation.

        Arguments:
            request: Authenticated REST request carrying an optional email field.

        Returns:
            Updated caller profile.

        Raises:
            ServiceUnavailable: If authoritative account persistence is unavailable.
            ValidationError: If input is invalid or the email is already occupied.
        """
        serializer = UserProfileUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        account = cast("User", request.user)
        email = serializer.validated_data.get("email")

        if isinstance(email, str):
            account.email = email
            try:
                with transaction.atomic(using="default"):
                    account.save(
                        using="default",
                        update_fields=["email", "updated_at"],
                    )
            except IntegrityError as error:
                raise ValidationError(
                    {"email": ["That email address is not available."]}
                ) from error
            except DatabaseError as error:
                raise ServiceUnavailable from error

        return Response(UserProfileSerializer(account).data, status=HTTPStatus.OK)

    @extend_schema(
        operation_id="user_profile_delete",
        summary="Delete the caller's account",
        description=(
            "Requires the current password and irreversibly deletes the authenticated account. No "
            "identifier can select another account."
        ),
        request=UserProfileDeletionSerializer,
        responses={
            HTTPStatus.NO_CONTENT: OpenApiResponse(
                response=None,
                description=(
                    "The account and its secondary token were removed. Digest-only activation "
                    "classification tombstones and detached JWT revocation metadata remain until "
                    "scheduled cleanup; operational logs and backups remain under their documented "
                    "retention policies."
                ),
            ),
            HTTPStatus.BAD_REQUEST: OpenApiResponse(
                response=ErrorEnvelopeSerializer,
                description=(
                    "The body is malformed, misses current_password, carries another field, or "
                    "supplies an incorrect current password."
                ),
                examples=[
                    error_example("Malformed JSON", PARSE_ERROR),
                    error_example(
                        "Missing current password",
                        VALIDATION_ERROR,
                        details={"current_password": ["This field is required."]},
                    ),
                    error_example(
                        "Incorrect current password",
                        VALIDATION_ERROR,
                        details={"current_password": ["The current password is incorrect."]},
                    ),
                ],
            ),
            HTTPStatus.UNAUTHORIZED: OpenApiResponse(
                response=ErrorEnvelopeSerializer,
                description="A credential was absent, malformed, expired, revoked, or inactive.",
                examples=[
                    error_example("Credential absent", NOT_AUTHENTICATED),
                    error_example("Authentication failed", AUTHENTICATION_FAILED),
                ],
            ),
            HTTPStatus.METHOD_NOT_ALLOWED: error_response(
                "The self-profile route supports only GET, PATCH, and DELETE.",
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
                "Authoritative account or credential state is unavailable.",
                "Service unavailable",
                SERVICE_UNAVAILABLE,
            ),
        },
    )
    def delete(self, request: Request) -> Response:
        """Irreversibly delete the authenticated caller after password confirmation.

        Locks the caller's authoritative account by immutable identifier, validates the current
        credential against that row, and removes it while the same transaction retains the lock.

        Arguments:
            request: Authenticated REST request carrying the current password.

        Returns:
            Empty successful response after deletion.

        Raises:
            AuthenticationFailed: If the authenticated account disappears before it can be locked.
            ServiceUnavailable: If authoritative account deletion is unavailable.
            ValidationError: If the current password is absent or incorrect.
        """
        serializer = UserProfileDeletionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        account_id = cast("User", request.user).pk
        current_password = cast("str", serializer.validated_data["current_password"])

        try:
            with transaction.atomic(using="default"):
                locked_account = (
                    User.objects.using("default").select_for_update().get(pk=account_id)
                )
                if not credential_policy.verify_encoded_password(
                    current_password,
                    locked_account.password,
                ):
                    raise ValidationError(
                        {"current_password": ["The current password is incorrect."]}
                    )

                locked_account.delete(using="default")
        except User.DoesNotExist as error:
            raise AuthenticationFailed from error
        except DatabaseError as error:
            raise ServiceUnavailable from error

        return Response(status=HTTPStatus.NO_CONTENT)
