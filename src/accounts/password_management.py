"""Password change and recovery endpoints.

Changes authenticated credentials and recovers locked-out accounts through account-bound,
single-use email tokens while revoking every identity issued before a password replacement.
"""

# mypy: disable-error-code=misc

import logging
import secrets
import uuid
from functools import partial
from hashlib import sha256
from http import HTTPStatus
from typing import TYPE_CHECKING, Any, cast, override

from celery.exceptions import CeleryError
from django.conf import settings
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import DatabaseError, connections, transaction
from django.db.models import Value
from django.db.models.functions import Lower
from django.utils import timezone
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema
from kombu.exceptions import KombuError
from rest_framework.authtoken.models import Token
from rest_framework.exceptions import AuthenticationFailed, ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.serializers import CharField, EmailField, UUIDField
from rest_framework.throttling import BaseThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken

from accounts.login_throttle import PostgresLoginThrottleStore, RollingWindowRule
from accounts.models import PasswordResetToken, User
from accounts.password_tokens import (
    create_password_reset_token,
    password_reset_token_digest,
    password_reset_token_is_expired,
    password_reset_token_matches,
    password_reset_token_remaining_seconds,
)
from accounts.registration_timing import (
    monotonic_now,
    wait_for_minimum_registration_duration,
)
from accounts.tasks import send_password_reset_email
from accounts.token_authentication import (
    ErrorEnvelopeSerializer,
    PasswordHashDisposition,
    classify_password_hash,
    error_example,
    error_response,
    parse_throttle_rate,
    trusted_client_address,
    verify_encoded_password,
)
from accounts.user_profiles import StrictFieldsSerializer, normalise_valid_email
from config.api_errors import (
    AUTHENTICATION_FAILED,
    INTERNAL_SERVER_ERROR,
    METHOD_NOT_ALLOWED,
    NOT_ACCEPTABLE,
    NOT_AUTHENTICATED,
    PARSE_ERROR,
    PASSWORD_RESET_TOKEN_EXPIRED,
    PASSWORD_RESET_TOKEN_FOREIGN,
    PASSWORD_RESET_TOKEN_MALFORMED,
    PASSWORD_RESET_TOKEN_USED,
    REQUEST_TOO_LARGE,
    SERVICE_UNAVAILABLE,
    THROTTLED,
    UNSUPPORTED_MEDIA_TYPE,
    VALIDATION_ERROR,
    PasswordResetTokenExpired,
    PasswordResetTokenForeign,
    PasswordResetTokenMalformed,
    PasswordResetTokenUsed,
    ServiceUnavailable,
)
from config.email import send_application_email
from config.logs import REQUEST_ID_META_KEY

if TYPE_CHECKING:
    from rest_framework.request import Request

logger = logging.getLogger(__name__)

PASSWORD_RESET_ACCEPTED_MESSAGE = (
    "If an account matches this address, a password reset email will be sent."  # noqa: S105
)
PASSWORD_RESET_VALIDATED_DATA_ATTRIBUTE = "_localforge_password_reset_validated_data"  # noqa: S105
MAXIMUM_OPERATION_ERROR_TYPE_CHARACTERS = 128


class PasswordResetRequestSerializer(StrictFieldsSerializer):
    """Validate one public password-reset request.

    Inherits from ``StrictFieldsSerializer`` and accepts only an email address normalized to the
    same form account persistence and authoritative identity lookup use.

    Attributes:
        email: Address that may belong to an active account.

    Members:
        validate_email: Normalize and revalidate the submitted address.
    """

    email = EmailField(max_length=254)

    def validate_email(self, value: str) -> str:
        """Normalize the submitted reset address.

        Applies storage normalization before identity lookup so public acceptance, throttle
        dimensions, and account resolution all consume the same valid address.

        Arguments:
            value: Syntactically valid submitted address.

        Returns:
            Valid normalized address.

        Raises:
            ValidationError: If normalization produces an invalid address.
        """
        return normalise_valid_email(value)


class PasswordResetResponseSerializer(StrictFieldsSerializer):
    """Describe the enumeration-resistant reset response.

    Inherits from ``StrictFieldsSerializer`` and exposes only the state-independent accepted
    statement shared by known and unknown addresses.

    Attributes:
        detail: State-independent accepted response text.

    Members:
        None beyond those inherited from ``Serializer``.
    """

    detail = CharField(read_only=True)


def validated_password_reset_data(request: Request) -> dict[str, Any]:
    """Validate and cache one complete password-reset request body.

    Runs the exact serializer before throttle admission and retains its normalized output for the
    view, ensuring malformed, incomplete, non-object, and undeclared-field bodies consume no quota.

    Arguments:
        request: Reset request being validated before admission or scheduling.

    Returns:
        Complete validated native field mapping.

    Raises:
        ValidationError: If the body is not exactly one valid email field.
    """
    cached = getattr(request, PASSWORD_RESET_VALIDATED_DATA_ATTRIBUTE, None)
    if isinstance(cached, dict):
        return cast("dict[str, Any]", cached)
    serializer = PasswordResetRequestSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    validated = cast("dict[str, Any]", serializer.validated_data)
    setattr(request, PASSWORD_RESET_VALIDATED_DATA_ATTRIBUTE, validated)
    return validated


def resolve_password_reset_account(email: str) -> User | None:
    """Resolve one reset address with PostgreSQL constraint semantics.

    Uses the primary database and row locking while comparing database lowercase expressions,
    matching the model's functional uniqueness rule exactly.

    Arguments:
        email: Valid normalized reset address.

    Returns:
        Matching account, or None when the address is unknown.

    Raises:
        DatabaseError: If authoritative state cannot be read.
    """
    try:
        return (
            User.objects.using("default")
            .select_for_update()
            .alias(reset_email=Lower("email"))
            .get(reset_email=Lower(Value(email)))
        )
    except User.DoesNotExist:
        return None


def password_reset_email_identity(email: str) -> str:
    """Build one stable reset-email admission identity.

    Uses PostgreSQL's lowercase result and no existence discriminator, so requests made before
    account creation constrain the same recipient identity afterwards.

    Arguments:
        email: Valid normalized reset address.

    Returns:
        Opaque normalized-email identity.

    Raises:
        DatabaseError: If PostgreSQL cannot normalize the address.
    """
    with connections["default"].cursor() as cursor:
        cursor.execute("SELECT LOWER(%s)", [email])
        normalized = cast("tuple[str]", cursor.fetchone())[0]
    return sha256(f"password-reset-email:{normalized}".encode()).hexdigest()


def password_reset_rules(
    email: str,
    account: User | None,
) -> tuple[RollingWindowRule, ...]:
    """Build reset-recipient admission dimensions.

    Always includes the stable normalized-email identity and adds the immutable account identity
    when one exists, using the same configured exact window for both.

    Arguments:
        email: Valid normalized reset address.
        account: Primary-resolved account, or None.

    Returns:
        Email and optional account rolling-window rules.

    Raises:
        DatabaseError: If PostgreSQL cannot normalize the address.
        ValueError: If the configured rate is invalid.
    """
    limit, window = parse_throttle_rate(cast("str", settings.PASSWORD_RESET_ACCOUNT_THROTTLE_RATE))
    rules = [
        RollingWindowRule(
            key=f"password-reset-email:{password_reset_email_identity(email)}",
            limit=limit,
            window_seconds=window,
        )
    ]
    if account is not None:
        identity = sha256(f"password-reset-account:{account.pk}".encode()).hexdigest()
        rules.append(
            RollingWindowRule(
                key=f"password-reset-account:{identity}",
                limit=limit,
                window_seconds=window,
            )
        )
    return tuple(rules)


class PasswordResetRequestThrottle(BaseThrottle):
    """Enforce authoritative reset request limits.

    Inherits from DRF's ``BaseThrottle`` and atomically charges client address, stable email, and
    optional immutable account dimensions only after the exact body validates.

    Attributes:
        retry_after_seconds: Primary-derived delay after rejection.

    Members:
        allow_request: Admit or reject every reset-request dimension.
        wait: Return the authoritative retry delay.
    """

    retry_after_seconds = 0

    @override
    def allow_request(self, request: Request, view: APIView) -> bool:
        """Atomically enforce valid reset-request dimensions.

        Ignores unsupported methods, validates POST bodies before charging quota, locks any resolved
        account, and fails closed if PostgreSQL cannot make the decision.

        Arguments:
            request: Password-reset request being admitted.
            view: Reset view applying the throttle.

        Returns:
            Whether authoritative shared state admitted the request.

        Raises:
            ServiceUnavailable: If authoritative admission state is unavailable.
            ValidationError: If the body is invalid.
            ValueError: If a configured rate is invalid.
        """
        del view
        if request.method != "POST":
            return True
        email = cast("str", validated_password_reset_data(request)["email"])
        address_limit, address_window = parse_throttle_rate(
            cast("str", settings.PASSWORD_RESET_ADDRESS_THROTTLE_RATE)
        )
        address = sha256(trusted_client_address(request).encode()).hexdigest()
        try:
            with transaction.atomic(using="default"):
                account = resolve_password_reset_account(email)
                rules = [
                    RollingWindowRule(
                        key=f"password-reset-request-address:{address}",
                        limit=address_limit,
                        window_seconds=address_window,
                    )
                ]
                rules.extend(password_reset_rules(email, account))
                correlation = str(request.META.get(REQUEST_ID_META_KEY, "uncorrelated"))
                decision = PostgresLoginThrottleStore(settings.LOGIN_THROTTLE_DATABASE_ALIAS).admit(
                    tuple(rules), member=f"{correlation}:{secrets.token_hex(16)}"
                )
        except DatabaseError as error:
            raise ServiceUnavailable from error
        self.retry_after_seconds = decision.retry_after_seconds
        return decision.admitted

    @override
    def wait(self) -> float | None:
        """Return the authoritative reset-request retry delay.

        Supplies DRF with the primary-derived duration for ``Retry-After``; the initialized zero is
        never observed by DRF because wait is consulted only after a denied decision.

        Arguments:
            None.

        Returns:
            Retry delay in seconds.
        """
        return float(self.retry_after_seconds)


def dispatch_password_reset_email(account_id: str, token: str) -> None:
    """Publish one password-reset email after commit.

    Sets broker expiry to the signed token lifetime remaining and contains publication failures
    without logging recipient, bearer, hash, or account data.

    Arguments:
        account_id: Immutable account key carried by the reset link.
        token: Django password-reset bearer value.

    Returns:
        None.
    """
    try:
        remaining = password_reset_token_remaining_seconds(token)
        send_password_reset_email.apply_async(
            args=(account_id, token),
            expires=remaining,
        )
    except ValueError:
        return
    except (CeleryError, KombuError, OSError) as error:
        error_type = type(error).__name__
    else:
        return
    logger.error(
        "Password reset email publication failed",
        extra={"task_error_type": error_type},
    )


def issue_password_reset(account: User) -> None:
    """Issue one digest-only password-reset record inside the current transaction.

    Uses Django's account-state-bound generator and registers expiring task publication only after
    commit so rollback removes both authoritative state and the pending side effect.

    Arguments:
        account: Active account receiving a reset link.

    Returns:
        None.

    Raises:
        DatabaseError: If the token record cannot be persisted.
    """
    token = create_password_reset_token(account)
    PasswordResetToken.objects.using("default").create(
        account=account,
        subject_id=account.pk,
        digest=password_reset_token_digest(token),
    )
    transaction.on_commit(
        partial(dispatch_password_reset_email, str(account.pk), token),
        using="default",
    )


def schedule_dummy_password_reset() -> None:
    """Schedule reset-shaped work that cannot deliver mail.

    Gives unknown and inactive outcomes Django token generation and queue publication with no
    authoritative digest record or recipient for the task to resolve.

    Arguments:
        None.

    Returns:
        None.
    """
    subject_id = uuid.uuid7()
    account = User(
        id=subject_id,
        username=f"reset-{subject_id}",
        email=f"reset-{subject_id}@localforge.invalid",
        is_active=True,
    )
    account.set_unusable_password()
    token = create_password_reset_token(account)
    transaction.on_commit(
        partial(dispatch_password_reset_email, str(subject_id), token),
        using="default",
    )


def request_password_reset(email: str) -> None:
    """Schedule one indistinguishable password-reset outcome.

    Locks the authoritative account and issues a real token only for an active match; unknown and
    inactive addresses publish equivalent dummy work that cannot deliver. A reset-token insert
    failure rolls its savepoint back and takes the same dummy path without changing the response.

    Arguments:
        email: Valid normalized reset address.

    Returns:
        None.

    Raises:
        ServiceUnavailable: If authoritative account lookup is unavailable.
    """
    try:
        with transaction.atomic(using="default"):
            account = resolve_password_reset_account(email)
            if account is not None and account.is_active:
                try:
                    with transaction.atomic(using="default"):
                        issue_password_reset(account)
                except DatabaseError as error:
                    logger.log(
                        logging.ERROR,
                        "Password reset token persistence failed",
                        extra={
                            "operation_error_type": type(error).__name__[
                                :MAXIMUM_OPERATION_ERROR_TYPE_CHARACTERS
                            ]
                        },
                    )
                    schedule_dummy_password_reset()
            else:
                schedule_dummy_password_reset()
    except DatabaseError as error:
        raise ServiceUnavailable from error


class PasswordResetRequestView(APIView):
    """Accept a password-reset request without disclosing account state.

    Inherits from DRF's ``APIView`` and returns one timed accepted representation for known,
    unknown, active, and inactive outcomes while only an active account can receive mail.

    Attributes:
        authentication_classes: Empty because locked-out callers hold no credential.
        permission_classes: Public access required for recovery.
        throttle_classes: Authoritative address and recipient admission.

    Members:
        initial: Capture the earliest DRF timing boundary.
        post: Validate and schedule one reset outcome.
    """

    authentication_classes: tuple[type, ...] = ()
    permission_classes = (AllowAny,)
    throttle_classes = (PasswordResetRequestThrottle,)
    _started_at: float

    @override
    def initial(self, request: Request, *args: Any, **kwargs: Any) -> None:
        """Capture timing before negotiation and admission.

        Starts the accepted-response floor at the earliest DRF view boundary while validation,
        throttle, and infrastructure failures remain immediate.

        Arguments:
            request: Initialized reset request.
            *args: Positional route arguments.
            **kwargs: Named route arguments.

        Returns:
            None.

        Raises:
            APIException: If negotiation, permissions, or throttling rejects the request.
        """
        self._started_at = monotonic_now()
        super().initial(request, *args, **kwargs)

    @extend_schema(
        operation_id="user_password_reset_request",
        summary="Request a password-reset email",
        description=(
            "Returns the same status, body, and configured minimum public duration whether an "
            "active account matches the normalized address or not. Only a matching active account "
            "receives an account-bound, expiring, single-use link. Reset-token insert failure "
            "rolls back partial state and takes the same accepted dummy-publication path."
        ),
        request=PasswordResetRequestSerializer,
        auth=[],
        responses={
            HTTPStatus.ACCEPTED: OpenApiResponse(
                response=PasswordResetResponseSerializer,
                description="The request was accepted without disclosing account state.",
                examples=[
                    OpenApiExample(
                        "Password reset accepted",
                        value={"detail": PASSWORD_RESET_ACCEPTED_MESSAGE},
                        response_only=True,
                    )
                ],
            ),
            HTTPStatus.BAD_REQUEST: OpenApiResponse(
                response=ErrorEnvelopeSerializer,
                description=(
                    "The body is malformed, misses email, carries another field, or supplies an "
                    "invalid email address."
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
                "Password reset requests accept only POST.",
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
                "The client address or recipient identity exceeded its configured reset rate.",
                "Too many requests",
                THROTTLED,
            ),
            HTTPStatus.INTERNAL_SERVER_ERROR: error_response(
                "An unexpected server failure was contained.",
                "Internal server error",
                INTERNAL_SERVER_ERROR,
            ),
            HTTPStatus.SERVICE_UNAVAILABLE: error_response(
                "Authoritative account lookup or reset-admission state is unavailable.",
                "Service unavailable",
                SERVICE_UNAVAILABLE,
            ),
        },
    )
    def post(self, request: Request) -> Response:
        """Validate and accept one password-reset request.

        Schedules real or dummy account-bound work from the normalized address, waits only the
        remainder of the configured floor, and returns no identifier or credential.

        Arguments:
            request: REST request carrying one email address.

        Returns:
            Enumeration-resistant accepted representation.

        Raises:
            ServiceUnavailable: If authoritative state is unavailable.
            ValidationError: If the submitted body is invalid.
        """
        validated = validated_password_reset_data(request)
        request_password_reset(cast("str", validated["email"]))
        wait_for_minimum_registration_duration(
            self._started_at,
            settings.PASSWORD_RESET_MINIMUM_RESPONSE_DURATION_SECONDS,
        )
        return Response(
            {"detail": PASSWORD_RESET_ACCEPTED_MESSAGE},
            status=HTTPStatus.ACCEPTED,
        )


class PasswordResetConfirmSerializer(StrictFieldsSerializer):
    """Validate one password-reset confirmation body.

    Inherits from ``StrictFieldsSerializer`` and accepts only the immutable account key, reset
    bearer, replacement password, and matching confirmation.

    Attributes:
        account: Immutable account key carried by the email link.
        token: Account-bound password-reset bearer.
        new_password: Replacement raw credential.
        new_password_confirm: Repeated replacement credential.

    Members:
        validate: Require matching replacement fields.
    """

    account = UUIDField()
    token = CharField(max_length=256, trim_whitespace=False)
    new_password = CharField(trim_whitespace=False, write_only=True)
    new_password_confirm = CharField(trim_whitespace=False, write_only=True)

    @override
    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        """Require the replacement confirmation to match.

        Leaves account-bound policy validation to the cached admission helper and locked
        confirmation transaction, where current account attributes are available.

        Arguments:
            attrs: Individually validated confirmation fields.

        Returns:
            Validated confirmation fields.

        Raises:
            ValidationError: If the replacement confirmation differs.
        """
        if attrs["new_password"] != attrs["new_password_confirm"]:
            raise ValidationError(
                {"new_password_confirm": ["The password confirmation does not match."]}
            )
        return attrs


def validated_password_reset_confirm_data(request: Request) -> dict[str, Any]:
    """Validate and cache one complete reset-confirm body.

    Applies exact shape, confirmation, and every configured password validator before throttle
    admission without account attributes. The locked confirmation transaction repeats the full
    validator set against the authenticated live account after token classification.

    Arguments:
        request: Reset-confirm request being admitted or applied.

    Returns:
        Complete validated native field mapping.

    Raises:
        ValidationError: If body shape, confirmation, or password policy is invalid.
    """
    attribute = "_localforge_password_reset_confirm_validated_data"
    cached = getattr(request, attribute, None)
    if isinstance(cached, dict):
        return cast("dict[str, Any]", cached)
    serializer = PasswordResetConfirmSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    validated = cast("dict[str, Any]", serializer.validated_data)
    try:
        validate_password(cast("str", validated["new_password"]), user=None)
    except DjangoValidationError as error:
        raise ValidationError({"new_password": list(error.messages)}) from error
    setattr(request, attribute, validated)
    return validated


class PasswordResetConfirmThrottle(BaseThrottle):
    """Enforce authoritative reset-confirm limits.

    Inherits from DRF's ``BaseThrottle`` and charges client address and submitted immutable account
    dimensions only after the complete body and replacement password validate.

    Attributes:
        retry_after_seconds: Primary-derived delay after rejection.

    Members:
        allow_request: Admit or reject both reset-confirm dimensions.
        wait: Return the authoritative retry delay.
    """

    retry_after_seconds = 0

    @override
    def allow_request(self, request: Request, view: APIView) -> bool:
        """Atomically enforce valid reset-confirm dimensions.

        Leaves unsupported methods uncharged, validates POST bodies first, and fails closed if
        account lookup or the primary-backed admission decision cannot complete.

        Arguments:
            request: Reset-confirm request being admitted.
            view: Confirmation view applying the throttle.

        Returns:
            Whether authoritative shared state admitted the request.

        Raises:
            ServiceUnavailable: If authoritative state is unavailable.
            ValidationError: If the body or replacement password is invalid.
            ValueError: If a configured rate is invalid.
        """
        del view
        if request.method != "POST":
            return True
        try:
            validated = validated_password_reset_confirm_data(request)
            account_id = cast("uuid.UUID", validated["account"])
            address_limit, address_window = parse_throttle_rate(
                cast("str", settings.PASSWORD_RESET_ADDRESS_THROTTLE_RATE)
            )
            account_limit, account_window = parse_throttle_rate(
                cast("str", settings.PASSWORD_RESET_ACCOUNT_THROTTLE_RATE)
            )
            address = sha256(trusted_client_address(request).encode()).hexdigest()
            account_identity = sha256(f"password-reset-account:{account_id}".encode()).hexdigest()
            correlation = str(request.META.get(REQUEST_ID_META_KEY, "uncorrelated"))
            decision = PostgresLoginThrottleStore(settings.LOGIN_THROTTLE_DATABASE_ALIAS).admit(
                (
                    RollingWindowRule(
                        key=f"password-reset-confirm-address:{address}",
                        limit=address_limit,
                        window_seconds=address_window,
                    ),
                    RollingWindowRule(
                        key=f"password-reset-confirm-account:{account_identity}",
                        limit=account_limit,
                        window_seconds=account_window,
                    ),
                ),
                member=f"{correlation}:{secrets.token_hex(16)}",
            )
        except DatabaseError as error:
            raise ServiceUnavailable from error
        self.retry_after_seconds = decision.retry_after_seconds
        return decision.admitted

    @override
    def wait(self) -> float | None:
        """Return the authoritative reset-confirm retry delay.

        Supplies DRF with the primary-derived duration for ``Retry-After``; the initialized zero is
        never observed by DRF because wait is consulted only after a denied decision.

        Arguments:
            None.

        Returns:
            Retry delay in seconds.
        """
        return float(self.retry_after_seconds)


def consume_outstanding_password_reset_tokens(account: User) -> None:
    """Invalidate every outstanding reset bearer for one locked account.

    Marks all live records used in the same primary transaction as a password replacement, making
    concurrent links and later replay classify consistently without retaining bearer material.

    Arguments:
        account: Primary account whose row is already locked.

    Returns:
        None.
    """
    PasswordResetToken.objects.using("default").filter(
        account=account,
        used_at__isnull=True,
    ).update(used_at=timezone.now())


def notify_password_changed(email: str) -> None:
    """Send one password-change notification after reset commit.

    Uses the failure-safe multipart email boundary with no action link or credential, so transport
    failure cannot undo the completed password replacement or expose sensitive values.

    Arguments:
        email: Current account address receiving the notification.

    Returns:
        None.
    """
    send_application_email(
        email,
        "Your LocalForge password changed",
        "Your password was changed through account recovery.",
    )


def _apply_locked_password_reset(
    account_id: uuid.UUID,
    record_id: int,
    digest: str,
    token: str,
    new_password: str,
) -> None:
    """Apply one preflighted reset under account-before-token locks when possible.

    Rechecks immutable token state even when the live account disappeared, then validates the
    replacement against a locked account, changes the password, consumes all links, revokes
    identities, and schedules notification.

    Arguments:
        account_id: Immutable account key authenticated by the request.
        record_id: Preflighted password-reset record key.
        digest: Digest of the submitted bearer.
        token: Submitted password-reset bearer.
        new_password: Validated replacement credential.

    Returns:
        None.

    Raises:
        PasswordResetTokenExpired: If the token ages out before locked validation.
        PasswordResetTokenForeign: If live account or token state no longer matches.
        PasswordResetTokenUsed: If a concurrent password change consumed the record.
        ValidationError: If the replacement matches the current password or fails policy.
    """
    with transaction.atomic(using="default"):
        try:
            account = User.objects.using("default").select_for_update().get(pk=account_id)
        except User.DoesNotExist:
            account = None
        record = (
            PasswordResetToken.objects.using("default")
            .select_for_update()
            .filter(
                pk=record_id,
                subject_id=account_id,
                digest=digest,
            )
            .first()
        )
        if record is None:
            raise PasswordResetTokenForeign
        if record.used_at is not None:
            raise PasswordResetTokenUsed
        if password_reset_token_is_expired(token):
            raise PasswordResetTokenExpired
        record_account_id = cast("uuid.UUID | None", vars(record)["account_id"])
        if account is None or record_account_id != account.pk:
            raise PasswordResetTokenForeign
        if not password_reset_token_matches(account, token):
            raise PasswordResetTokenForeign

        disposition = classify_password_hash(account.password)
        accepted_profile = disposition is not PasswordHashDisposition.RESET_REQUIRED
        if accepted_profile and verify_encoded_password(new_password, account.password):
            raise ValidationError(
                {"new_password": ["The new password must differ from the current password."]}
            )
        try:
            validate_password(new_password, user=account)
        except DjangoValidationError as error:
            raise ValidationError({"new_password": list(error.messages)}) from error

        account.set_password(new_password)
        account.save(using="default", update_fields=("password", "updated_at"))
        consume_outstanding_password_reset_tokens(account)
        revoke_account_credentials(account)
        transaction.on_commit(
            partial(notify_password_changed, account.email),
            using="default",
        )


def confirm_password_reset(validated_data: dict[str, Any]) -> None:
    """Apply one valid unused password-reset token.

    Classifies malformed, foreign, expired, and used bearers distinctly, then delegates locked
    replacement, credential revocation, and credential-free notification as one transaction.

    Arguments:
        validated_data: Account key, token, replacement, and confirmation fields.

    Returns:
        None.

    Raises:
        PasswordResetTokenExpired: If the issued token exceeded its lifetime.
        PasswordResetTokenForeign: If token state does not match the submitted account.
        PasswordResetTokenMalformed: If the token structure is invalid.
        PasswordResetTokenUsed: If a password change already consumed the token.
        ServiceUnavailable: If authoritative state is unavailable.
        ValidationError: If the replacement matches the current password or fails policy.
    """
    account_id = cast("uuid.UUID", validated_data["account"])
    token = cast("str", validated_data["token"])
    new_password = cast("str", validated_data["new_password"])
    try:
        try:
            expired = password_reset_token_is_expired(token)
        except (TypeError, ValueError) as error:
            raise PasswordResetTokenMalformed from error
        digest = password_reset_token_digest(token)
        preflight = (
            PasswordResetToken.objects.using("default")
            .filter(subject_id=account_id, digest=digest)
            .values_list("pk", "used_at")
            .first()
        )
        if preflight is None:
            raise PasswordResetTokenForeign
        record_id, used_at = preflight
        if used_at is not None:
            raise PasswordResetTokenUsed
        if expired:
            raise PasswordResetTokenExpired

        _apply_locked_password_reset(
            account_id,
            record_id,
            digest,
            token,
            new_password,
        )
    except DatabaseError as error:
        raise ServiceUnavailable from error


class PasswordResetConfirmView(APIView):
    """Confirm an account-bound password reset.

    Inherits from DRF's ``APIView`` and exposes public recovery while authoritative primary state
    enforces one-time use, account binding, expiry, replacement policy, and credential revocation.

    Attributes:
        authentication_classes: Empty because recovery callers hold no credential.
        permission_classes: Public access required for recovery.
        throttle_classes: Authoritative address and account confirmation admission.

    Members:
        post: Validate and apply one password reset.
    """

    authentication_classes: tuple[type, ...] = ()
    permission_classes = (AllowAny,)
    throttle_classes = (PasswordResetConfirmThrottle,)

    @extend_schema(
        operation_id="user_password_reset_confirm",
        summary="Confirm a password reset",
        description=(
            "Accepts the account key and token from the email with a replacement password. Success "
            "consumes every outstanding reset link, revokes all existing credentials and sessions, "
            "and sends a credential-free password-change notification. Admission validation is "
            "account-independent; complete account-sensitive validation runs only after the "
            "bearer and locked account authenticate."
        ),
        request=PasswordResetConfirmSerializer,
        auth=[],
        responses={
            HTTPStatus.NO_CONTENT: OpenApiResponse(
                response=None,
                description=(
                    "The password changed, all reset links and existing identities were revoked, "
                    "and a password-change notification was scheduled."
                ),
            ),
            HTTPStatus.BAD_REQUEST: OpenApiResponse(
                response=ErrorEnvelopeSerializer,
                description=(
                    "The body or replacement is invalid, or the reset token is malformed, foreign, "
                    "expired, or already used."
                ),
                examples=[
                    error_example("Malformed JSON", PARSE_ERROR),
                    error_example(
                        "Password policy failure",
                        VALIDATION_ERROR,
                        details={
                            "new_password": [
                                "This password is too short. It must contain at least 8 characters."
                            ]
                        },
                    ),
                    error_example(
                        "Reset token expired",
                        PASSWORD_RESET_TOKEN_EXPIRED,
                    ),
                    error_example(
                        "Reset token belongs to another account",
                        PASSWORD_RESET_TOKEN_FOREIGN,
                    ),
                    error_example(
                        "Reset token malformed",
                        PASSWORD_RESET_TOKEN_MALFORMED,
                    ),
                    error_example(
                        "Reset token already used",
                        PASSWORD_RESET_TOKEN_USED,
                    ),
                ],
            ),
            HTTPStatus.METHOD_NOT_ALLOWED: error_response(
                "Password reset confirmation accepts only POST.",
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
                "The client address or account exceeded its configured reset-confirm rate.",
                "Too many requests",
                THROTTLED,
            ),
            HTTPStatus.INTERNAL_SERVER_ERROR: error_response(
                "An unexpected server failure was contained.",
                "Internal server error",
                INTERNAL_SERVER_ERROR,
            ),
            HTTPStatus.SERVICE_UNAVAILABLE: error_response(
                "Authoritative account, credential, token, or admission state is unavailable.",
                "Service unavailable",
                SERVICE_UNAVAILABLE,
            ),
        },
    )
    def post(self, request: Request) -> Response:
        """Validate and apply one password-reset confirmation.

        Reuses the pre-admission validated body, then performs token classification, locking,
        replacement, revocation, and notification scheduling through the authoritative helper.

        Arguments:
            request: REST request carrying the complete reset confirmation.

        Returns:
            Empty successful response.

        Raises:
            APIException: If validation, token classification, admission, or persistence fails.
        """
        validated = validated_password_reset_confirm_data(request)
        confirm_password_reset(validated)
        return Response(status=HTTPStatus.NO_CONTENT)


class PasswordChangeSerializer(StrictFieldsSerializer):
    """Validate one authenticated password replacement body.

    Inherits from ``StrictFieldsSerializer`` and accepts only the current credential, its
    replacement, and confirmation while keeping every password write-only.

    Attributes:
        current_password: Existing raw credential.
        new_password: Replacement raw credential.
        new_password_confirm: Repeated replacement credential.

    Members:
        validate: Require matching replacement fields.
    """

    current_password = CharField(trim_whitespace=False, write_only=True)
    new_password = CharField(trim_whitespace=False, write_only=True)
    new_password_confirm = CharField(trim_whitespace=False, write_only=True)

    @override
    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        """Require the replacement confirmation to match.

        Leaves current-password verification and account-aware configured validators inside the
        locked transaction where concurrent password changes cannot invalidate their result.

        Arguments:
            attrs: Individually validated password fields.

        Returns:
            Validated password fields.

        Raises:
            ValidationError: If the replacement confirmation differs.
        """
        if attrs["new_password"] != attrs["new_password_confirm"]:
            raise ValidationError(
                {"new_password_confirm": ["The password confirmation does not match."]}
            )
        return attrs


def revoke_account_credentials(account: User) -> None:
    """Revoke every token credential issued before a password replacement.

    Deletes the secondary DRF token and blacklists every outstanding refresh credential on the
    primary. Access tokens and Django sessions are rejected by their embedded password hashes.

    Arguments:
        account: Locked account whose password has just changed.

    Returns:
        None.

    Raises:
        DatabaseError: If authoritative credential state cannot be updated.
    """
    Token.objects.using("default").filter(user=account).delete()
    outstanding = list(
        OutstandingToken.objects.using("default").select_for_update().filter(user=account)
    )
    BlacklistedToken.objects.using("default").bulk_create(
        [BlacklistedToken(token=token) for token in outstanding],
        ignore_conflicts=True,
    )


def change_password(account_id: object, validated_data: dict[str, Any]) -> None:
    """Replace one authenticated account password under a primary row lock.

    Rechecks the current credential against an accepted stored profile, applies every configured
    validator, persists a distinct replacement, and revokes all identities in one transaction.

    Arguments:
        account_id: Immutable key resolved by request authentication.
        validated_data: Current, replacement, and confirmation password fields.

    Returns:
        None.

    Raises:
        AuthenticationFailed: If the account disappeared before locking.
        ServiceUnavailable: If authoritative state cannot be read or updated.
        ValidationError: If the current password or replacement is invalid.
    """
    current_password = cast("str", validated_data["current_password"])
    new_password = cast("str", validated_data["new_password"])
    try:
        with transaction.atomic(using="default"):
            try:
                account = User.objects.using("default").select_for_update().get(pk=account_id)
            except User.DoesNotExist as error:
                raise AuthenticationFailed from error

            disposition = classify_password_hash(account.password)
            if disposition is PasswordHashDisposition.RESET_REQUIRED or not verify_encoded_password(
                current_password,
                account.password,
            ):
                raise ValidationError({"current_password": ["The current password is incorrect."]})
            if verify_encoded_password(new_password, account.password):
                raise ValidationError(
                    {"new_password": ["The new password must differ from the current password."]}
                )
            try:
                validate_password(new_password, user=account)
            except DjangoValidationError as error:
                raise ValidationError({"new_password": list(error.messages)}) from error

            account.set_password(new_password)
            account.save(using="default", update_fields=("password", "updated_at"))
            consume_outstanding_password_reset_tokens(account)
            revoke_account_credentials(account)
    except DatabaseError as error:
        raise ServiceUnavailable from error


class PasswordChangeView(APIView):
    """Replace the authenticated caller's password and identity set.

    Inherits from DRF's ``APIView`` and requires central authentication. The caller receives no
    exemption: the credential used for this request and every other existing identity are revoked.

    Attributes:
        permission_classes: Authenticated callers only.

    Members:
        post: Validate, replace, and revoke the caller's credentials.
    """

    permission_classes = (IsAuthenticated,)

    @extend_schema(
        operation_id="user_password_change",
        summary="Change the caller's password",
        description=(
            "Requires the current password, a distinct replacement, and confirmation. Every "
            "configured password validator applies. Success invalidates all DRF tokens, JSON web "
            "tokens, and Django sessions, including the caller credential used for this request; "
            "the client must authenticate again."
        ),
        request=PasswordChangeSerializer,
        responses={
            HTTPStatus.NO_CONTENT: OpenApiResponse(
                response=None,
                description=(
                    "The password changed and every existing credential and session was revoked, "
                    "including the caller's."
                ),
            ),
            HTTPStatus.BAD_REQUEST: OpenApiResponse(
                response=ErrorEnvelopeSerializer,
                description=(
                    "The body is malformed, misses a field, carries another field, supplies an "
                    "incorrect current password, repeats the current password, mismatches "
                    "confirmation, or fails configured password validation."
                ),
                examples=[
                    error_example("Malformed JSON", PARSE_ERROR),
                    error_example(
                        "Incorrect current password",
                        VALIDATION_ERROR,
                        details={"current_password": ["The current password is incorrect."]},
                    ),
                    error_example(
                        "Unchanged password",
                        VALIDATION_ERROR,
                        details={
                            "new_password": [
                                "The new password must differ from the current password."
                            ]
                        },
                    ),
                    error_example(
                        "Password policy failure",
                        VALIDATION_ERROR,
                        details={
                            "new_password": [
                                "This password is too short. It must contain at least 8 characters."
                            ]
                        },
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
                "Password change accepts only POST.",
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
                "Authoritative account or credential state is unavailable.",
                "Service unavailable",
                SERVICE_UNAVAILABLE,
            ),
        },
    )
    def post(self, request: Request) -> Response:
        """Validate and replace the authenticated caller's password.

        Uses the account identity already authenticated for this request, then performs all
        credential-sensitive checks and writes against a freshly locked primary row.

        Arguments:
            request: Authenticated REST request carrying the password fields.

        Returns:
            Empty successful response.

        Raises:
            AuthenticationFailed: If the account disappeared before locking.
            ServiceUnavailable: If authoritative state is unavailable.
            ValidationError: If a password field fails validation.
        """
        serializer = PasswordChangeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        change_password(cast("User", request.user).pk, serializer.validated_data)
        return Response(status=HTTPStatus.NO_CONTENT)
