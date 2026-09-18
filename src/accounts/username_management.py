"""Username change and recovery endpoints.

Changes the authenticated login identifier and recovers it through account-bound, single-use
email tokens while deliberately preserving every credential issued before a successful rename.
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
from django.contrib.auth.validators import UnicodeUsernameValidator
from django.db import DatabaseError, IntegrityError, connections, transaction
from django.db.models import Value
from django.db.models.functions import Lower
from django.utils import timezone
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema
from kombu.exceptions import KombuError
from rest_framework.exceptions import AuthenticationFailed, ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.serializers import CharField, EmailField, UUIDField
from rest_framework.throttling import BaseThrottle
from rest_framework.views import APIView

from accounts.api_throttling import (
    AnonymousApiThrottle,
    AuthenticationRecoveryThrottle,
)
from accounts.login_throttle import PostgresLoginThrottleStore, RollingWindowRule
from accounts.models import User, UsernameResetToken
from accounts.registration_timing import (
    monotonic_now,
    wait_for_minimum_registration_duration,
)
from accounts.request_throttling import parse_throttle_rate, trusted_client_address
from accounts.tasks import send_username_reset_email
from accounts.token_authentication import (
    ErrorEnvelopeSerializer,
    PasswordHashDisposition,
    classify_password_hash,
    error_example,
    error_response,
    verify_encoded_password,
)
from accounts.user_profiles import StrictFieldsSerializer, normalise_valid_email
from accounts.username_tokens import (
    create_username_reset_token,
    username_reset_token_digest,
    username_reset_token_is_expired,
    username_reset_token_matches,
    username_reset_token_remaining_seconds,
)
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
    USERNAME_RESET_TOKEN_EXPIRED,
    USERNAME_RESET_TOKEN_FOREIGN,
    USERNAME_RESET_TOKEN_MALFORMED,
    USERNAME_RESET_TOKEN_USED,
    VALIDATION_ERROR,
    ServiceUnavailable,
    UsernameResetTokenExpired,
    UsernameResetTokenForeign,
    UsernameResetTokenMalformed,
    UsernameResetTokenUsed,
)
from config.email import send_application_email
from config.logs import REQUEST_ID_META_KEY

if TYPE_CHECKING:
    from rest_framework.request import Request

logger = logging.getLogger(__name__)

USERNAME_RESET_ACCEPTED_MESSAGE = (
    "If an account matches this address, a username reset email will be sent."
)
USERNAME_UNAVAILABLE_MESSAGE = "That username is not available."
USERNAME_RESET_VALIDATED_DATA_ATTRIBUTE = "_localforge_username_reset_validated_data"
MAXIMUM_OPERATION_ERROR_TYPE_CHARACTERS = 128
username_validator = UnicodeUsernameValidator()


class UsernameResetRequestSerializer(StrictFieldsSerializer):
    """Validate one public username-reset request.

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


class UsernameResetResponseSerializer(StrictFieldsSerializer):
    """Describe the enumeration-resistant username-reset response.

    Inherits from ``StrictFieldsSerializer`` and exposes only the state-independent accepted
    statement shared by known and unknown addresses.

    Attributes:
        detail: State-independent accepted response text.

    Members:
        None beyond those inherited from ``Serializer``.
    """

    detail = CharField(read_only=True)


def validated_username_reset_data(request: Request) -> dict[str, Any]:
    """Validate and cache one complete username-reset request body.

    Runs the exact serializer before throttle admission and retains its normalized output for the
    view, ensuring malformed, incomplete, non-object, and undeclared-field bodies consume no quota.

    Arguments:
        request: Reset request being validated before admission or scheduling.

    Returns:
        Complete validated native field mapping.

    Raises:
        ValidationError: If the body is not exactly one valid email field.
    """
    cached = getattr(request, USERNAME_RESET_VALIDATED_DATA_ATTRIBUTE, None)
    if isinstance(cached, dict):
        return cast("dict[str, Any]", cached)
    serializer = UsernameResetRequestSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    validated = cast("dict[str, Any]", serializer.validated_data)
    setattr(request, USERNAME_RESET_VALIDATED_DATA_ATTRIBUTE, validated)
    return validated


def resolve_username_reset_account(email: str) -> User | None:
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


def username_reset_email_identity(email: str) -> str:
    """Build one stable username-reset email admission identity.

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
    return sha256(f"username-reset-email:{normalized}".encode()).hexdigest()


def username_reset_rules(
    email: str,
    account: User | None,
) -> tuple[RollingWindowRule, ...]:
    """Build username-reset recipient admission dimensions.

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
    limit, window = parse_throttle_rate(cast("str", settings.USERNAME_RESET_ACCOUNT_THROTTLE_RATE))
    rules = [
        RollingWindowRule(
            key=f"username-reset-email:{username_reset_email_identity(email)}",
            limit=limit,
            window_seconds=window,
        )
    ]
    if account is not None:
        identity = sha256(f"username-reset-account:{account.pk}".encode()).hexdigest()
        rules.append(
            RollingWindowRule(
                key=f"username-reset-account:{identity}",
                limit=limit,
                window_seconds=window,
            )
        )
    return tuple(rules)


class UsernameResetRequestThrottle(BaseThrottle):
    """Enforce authoritative username-reset request limits.

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
        """Atomically enforce valid username-reset request dimensions.

        Ignores unsupported methods, validates POST bodies before charging quota, locks any resolved
        account, and fails closed if PostgreSQL cannot make the decision.

        Arguments:
            request: Username-reset request being admitted.
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
        email = cast("str", validated_username_reset_data(request)["email"])
        address_limit, address_window = parse_throttle_rate(
            cast("str", settings.USERNAME_RESET_ADDRESS_THROTTLE_RATE)
        )
        address = sha256(trusted_client_address(request).encode()).hexdigest()
        try:
            with transaction.atomic(using="default"):
                account = resolve_username_reset_account(email)
                rules = [
                    RollingWindowRule(
                        key=f"username-reset-request-address:{address}",
                        limit=address_limit,
                        window_seconds=address_window,
                    )
                ]
                rules.extend(username_reset_rules(email, account))
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


def dispatch_username_reset_email(account_id: str, token: str) -> None:
    """Publish one username-reset email after commit.

    Sets broker expiry to the signed token lifetime remaining and contains publication failures
    without logging recipient, bearer, hash, username, or account data.

    Arguments:
        account_id: Immutable account key carried by the reset link.
        token: Django username-reset bearer value.

    Returns:
        None.
    """
    try:
        remaining = username_reset_token_remaining_seconds(token)
        send_username_reset_email.apply_async(
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
        "Username reset email publication failed",
        extra={"task_error_type": error_type},
    )


def issue_username_reset(account: User) -> None:
    """Issue one digest-only username-reset record inside the current transaction.

    Uses Django's account-state-bound generator and registers expiring task publication only after
    commit so rollback removes both authoritative state and the pending side effect.

    Arguments:
        account: Active account receiving a reset link.

    Returns:
        None.

    Raises:
        DatabaseError: If the token record cannot be persisted.
    """
    token = create_username_reset_token(account)
    UsernameResetToken.objects.using("default").create(
        account=account,
        subject_id=account.pk,
        digest=username_reset_token_digest(token),
    )
    transaction.on_commit(
        partial(dispatch_username_reset_email, str(account.pk), token),
        using="default",
    )


def schedule_dummy_username_reset() -> None:
    """Schedule username-reset-shaped work that cannot deliver mail.

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
    token = create_username_reset_token(account)
    transaction.on_commit(
        partial(dispatch_username_reset_email, str(subject_id), token),
        using="default",
    )


def request_username_reset(email: str) -> None:
    """Schedule one indistinguishable username-reset outcome.

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
            account = resolve_username_reset_account(email)
            if account is not None and account.is_active:
                try:
                    with transaction.atomic(using="default"):
                        issue_username_reset(account)
                except DatabaseError as error:
                    logger.log(
                        logging.ERROR,
                        "Username reset token persistence failed",
                        extra={
                            "operation_error_type": type(error).__name__[
                                :MAXIMUM_OPERATION_ERROR_TYPE_CHARACTERS
                            ]
                        },
                    )
                    schedule_dummy_username_reset()
            else:
                schedule_dummy_username_reset()
    except DatabaseError as error:
        raise ServiceUnavailable from error


class UsernameResetRequestView(APIView):
    """Accept a username-reset request without disclosing account state.

    Inherits from ``APIView`` and returns one timed accepted representation for known, unknown,
    active, and inactive outcomes while only an active account can receive mail.

    Attributes:
        authentication_classes: Empty because callers may not know the login identifier.
        permission_classes: Public access required for recovery.
        throttle_classes: Authoritative recovery admission plus shared anonymous and authentication
            scopes.

    Members:
        initial: Capture the earliest DRF timing boundary.
        post: Validate and schedule one reset outcome.
    """

    authentication_classes: tuple[type, ...] = ()
    permission_classes = (AllowAny,)
    throttle_classes = (
        UsernameResetRequestThrottle,
        AnonymousApiThrottle,
        AuthenticationRecoveryThrottle,
    )
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
        operation_id="user_username_reset_request",
        summary="Request a username-reset email",
        description=(
            "Returns the same status, body, and configured minimum public duration whether an "
            "active account matches the normalized address or not. Only a matching active account "
            "receives an account-bound, expiring, single-use link. Token insert failure rolls back "
            "partial state and takes the same accepted dummy-publication path."
        ),
        request=UsernameResetRequestSerializer,
        auth=[],
        responses={
            HTTPStatus.ACCEPTED: OpenApiResponse(
                response=UsernameResetResponseSerializer,
                description="The request was accepted without disclosing account state.",
                examples=[
                    OpenApiExample(
                        "Username reset accepted",
                        value={"detail": USERNAME_RESET_ACCEPTED_MESSAGE},
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
                "Username reset requests accept only POST.",
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
        """Validate and accept one username-reset request.

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
        validated = validated_username_reset_data(request)
        request_username_reset(cast("str", validated["email"]))
        wait_for_minimum_registration_duration(
            self._started_at,
            settings.USERNAME_RESET_MINIMUM_RESPONSE_DURATION_SECONDS,
        )
        return Response(
            {"detail": USERNAME_RESET_ACCEPTED_MESSAGE},
            status=HTTPStatus.ACCEPTED,
        )


class UsernameResetConfirmSerializer(StrictFieldsSerializer):
    """Validate one username-reset confirmation body.

    Inherits from ``StrictFieldsSerializer`` and accepts only the immutable account key, reset
    bearer, and replacement username.

    Attributes:
        account: Immutable account key carried by the email link.
        token: Account-bound username-reset bearer.
        new_username: Replacement login identifier.

    Members:
        None beyond field validation.
    """

    account = UUIDField()
    token = CharField(max_length=256, trim_whitespace=False)
    new_username = CharField(
        max_length=150,
        trim_whitespace=False,
        validators=(username_validator,),
    )


def validated_username_reset_confirm_data(request: Request) -> dict[str, Any]:
    """Validate and cache one complete username-reset confirmation body.

    Applies exact shape and account-independent username format before throttle admission while
    authoritative PostgreSQL uniqueness runs after bearer authentication and account locking.

    Arguments:
        request: Reset-confirm request being admitted or applied.

    Returns:
        Complete validated native field mapping.

    Raises:
        ValidationError: If body shape or username format is invalid.
    """
    attribute = "_localforge_username_reset_confirm_validated_data"
    cached = getattr(request, attribute, None)
    if isinstance(cached, dict):
        return cast("dict[str, Any]", cached)
    serializer = UsernameResetConfirmSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    validated = cast("dict[str, Any]", serializer.validated_data)
    setattr(request, attribute, validated)
    return validated


class UsernameResetConfirmThrottle(BaseThrottle):
    """Enforce authoritative username-reset confirmation limits.

    Inherits from ``BaseThrottle`` and charges client address and submitted immutable account
    dimensions only after the complete body and username format validate.

    Attributes:
        retry_after_seconds: Primary-derived delay after rejection.

    Members:
        allow_request: Admit or reject both reset-confirm dimensions.
        wait: Return the authoritative retry delay.
    """

    retry_after_seconds = 0

    @override
    def allow_request(self, request: Request, view: APIView) -> bool:
        """Atomically enforce valid username-reset confirmation dimensions.

        Leaves unsupported methods uncharged, validates POST bodies first, and fails closed if the
        primary-backed admission decision cannot complete.

        Arguments:
            request: Reset-confirm request being admitted.
            view: Confirmation view applying the throttle.

        Returns:
            Whether authoritative shared state admitted the request.

        Raises:
            ServiceUnavailable: If authoritative state is unavailable.
            ValidationError: If the body or replacement username is invalid.
            ValueError: If a configured rate is invalid.
        """
        del view
        if request.method != "POST":
            return True
        try:
            validated = validated_username_reset_confirm_data(request)
            account_id = cast("uuid.UUID", validated["account"])
            address_limit, address_window = parse_throttle_rate(
                cast("str", settings.USERNAME_RESET_ADDRESS_THROTTLE_RATE)
            )
            account_limit, account_window = parse_throttle_rate(
                cast("str", settings.USERNAME_RESET_ACCOUNT_THROTTLE_RATE)
            )
            address = sha256(trusted_client_address(request).encode()).hexdigest()
            account_identity = sha256(f"username-reset-account:{account_id}".encode()).hexdigest()
            correlation = str(request.META.get(REQUEST_ID_META_KEY, "uncorrelated"))
            decision = PostgresLoginThrottleStore(settings.LOGIN_THROTTLE_DATABASE_ALIAS).admit(
                (
                    RollingWindowRule(
                        key=f"username-reset-confirm-address:{address}",
                        limit=address_limit,
                        window_seconds=address_window,
                    ),
                    RollingWindowRule(
                        key=f"username-reset-confirm-account:{account_identity}",
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


def username_is_available(username: str, *, excluding: uuid.UUID) -> bool:
    """Check username availability with the database constraint's exact semantics.

    Compares PostgreSQL ``LOWER`` expressions and excludes the account being renamed, allowing a
    caller to change letter case while rejecting every occupied cross-account variant.

    Arguments:
        username: Valid replacement username.
        excluding: Account key allowed to retain its own lowercase identity.

    Returns:
        Whether no other account owns the lowercase identifier.

    Raises:
        DatabaseError: If authoritative account state cannot be read.
    """
    return not (
        User.objects.using("default")
        .exclude(pk=excluding)
        .alias(candidate_username=Lower("username"))
        .filter(candidate_username=Lower(Value(username)))
        .exists()
    )


def consume_outstanding_username_reset_tokens(account: User) -> None:
    """Invalidate every outstanding username-reset bearer for one locked account.

    Marks all live records used in the same primary transaction as a username replacement, making
    concurrent links and later replay classify consistently without retaining bearer material.

    Arguments:
        account: Primary account whose row is already locked.

    Returns:
        None.
    """
    UsernameResetToken.objects.using("default").filter(
        account=account,
        used_at__isnull=True,
    ).update(used_at=timezone.now())


def notify_username_changed(email: str) -> None:
    """Send one username-change notification after commit.

    Uses the failure-safe multipart email boundary with no old or new username, action link, or
    credential so transport failure cannot undo the completed identifier change.

    Arguments:
        email: Current account address receiving the notification.

    Returns:
        None.
    """
    send_application_email(
        email,
        "Your LocalForge username changed",
        "Your username was changed.",
    )


def dispatch_username_change_notification(email: str) -> None:
    """Publish one username-change notification without escaping after commit.

    Contains unexpected notification failures and records only their bounded type, preserving the
    committed username and bearer state without exposing account or credential values.

    Arguments:
        email: Current account address receiving the notification.

    Returns:
        None.
    """
    try:
        notify_username_changed(email)
    except Exception as error:  # noqa: BLE001
        logger.log(
            logging.ERROR,
            "Username change notification failed",
            extra={
                "operation_error_type": type(error).__name__[
                    :MAXIMUM_OPERATION_ERROR_TYPE_CHARACTERS
                ]
            },
        )


def _save_username(account: User, new_username: str) -> None:
    """Persist one username under the database uniqueness constraint.

    Performs the authoritative pre-check with PostgreSQL lowercase semantics and translates a
    concurrent constraint winner into the same neutral per-field validation response.

    Arguments:
        account: Locked primary account being renamed.
        new_username: Format-valid replacement identifier.

    Returns:
        None.

    Raises:
        ValidationError: If another account owns the lowercase identifier.
        DatabaseError: If authoritative state cannot be read or written.
    """
    if not username_is_available(new_username, excluding=account.pk):
        raise ValidationError({"new_username": [USERNAME_UNAVAILABLE_MESSAGE]})
    account.username = new_username
    try:
        with transaction.atomic(using="default"):
            account.save(using="default", update_fields=("username", "updated_at"))
    except IntegrityError as error:
        raise ValidationError({"new_username": [USERNAME_UNAVAILABLE_MESSAGE]}) from error


def _apply_locked_username_reset(
    account_id: uuid.UUID,
    record_id: int,
    digest: str,
    token: str,
    new_username: str,
) -> None:
    """Apply one preflighted username reset under account-before-token locks.

    Rechecks immutable token state even when the live account disappeared, validates authoritative
    uniqueness, consumes every link, preserves credentials, and schedules notification.

    Arguments:
        account_id: Immutable account key authenticated by the request.
        record_id: Preflighted username-reset record key.
        digest: Digest of the submitted bearer.
        token: Submitted username-reset bearer.
        new_username: Validated replacement identifier.

    Returns:
        None.

    Raises:
        UsernameResetTokenExpired: If the token ages out before locked validation.
        UsernameResetTokenForeign: If live account or token state no longer matches.
        UsernameResetTokenUsed: If a concurrent username change consumed the record.
        ValidationError: If the replacement username is unavailable.
    """
    with transaction.atomic(using="default"):
        try:
            account = User.objects.using("default").select_for_update().get(pk=account_id)
        except User.DoesNotExist:
            account = None
        record = (
            UsernameResetToken.objects.using("default")
            .select_for_update()
            .filter(
                pk=record_id,
                subject_id=account_id,
                digest=digest,
            )
            .first()
        )
        if record is None:
            raise UsernameResetTokenForeign
        if record.used_at is not None:
            raise UsernameResetTokenUsed
        if username_reset_token_is_expired(token):
            raise UsernameResetTokenExpired
        record_account_id = cast("uuid.UUID | None", vars(record)["account_id"])
        if account is None or record_account_id != account.pk:
            raise UsernameResetTokenForeign
        if not username_reset_token_matches(account, token):
            raise UsernameResetTokenForeign

        _save_username(account, new_username)
        consume_outstanding_username_reset_tokens(account)
        transaction.on_commit(
            partial(dispatch_username_change_notification, account.email),
            using="default",
        )


def confirm_username_reset(validated_data: dict[str, Any]) -> None:
    """Apply one valid unused username-reset token.

    Classifies malformed, foreign, expired, and used bearers distinctly, then delegates locked
    uniqueness, persistence, link consumption, and notification as one transaction.

    Arguments:
        validated_data: Account key, token, and replacement username fields.

    Returns:
        None.

    Raises:
        UsernameResetTokenExpired: If the issued token exceeded its lifetime.
        UsernameResetTokenForeign: If token state does not match the submitted account.
        UsernameResetTokenMalformed: If the token structure is invalid.
        UsernameResetTokenUsed: If a username change already consumed the token.
        ServiceUnavailable: If authoritative state is unavailable.
        ValidationError: If the replacement username is unavailable.
    """
    account_id = cast("uuid.UUID", validated_data["account"])
    token = cast("str", validated_data["token"])
    new_username = cast("str", validated_data["new_username"])
    try:
        try:
            expired = username_reset_token_is_expired(token)
        except (TypeError, ValueError) as error:
            raise UsernameResetTokenMalformed from error
        digest = username_reset_token_digest(token)
        preflight = (
            UsernameResetToken.objects.using("default")
            .filter(subject_id=account_id, digest=digest)
            .values_list("pk", "used_at")
            .first()
        )
        if preflight is None:
            raise UsernameResetTokenForeign
        record_id, used_at = preflight
        if used_at is not None:
            raise UsernameResetTokenUsed
        if expired:
            raise UsernameResetTokenExpired

        _apply_locked_username_reset(
            account_id,
            record_id,
            digest,
            token,
            new_username,
        )
    except DatabaseError as error:
        raise ServiceUnavailable from error


class UsernameResetConfirmView(APIView):
    """Confirm an account-bound username reset.

    Inherits from ``APIView`` and exposes public recovery while authoritative primary state
    enforces one-time use, account binding, expiry, format, and case-insensitive uniqueness.

    Attributes:
        authentication_classes: Empty because recovery callers may not know the login identifier.
        permission_classes: Public access required for recovery.
        throttle_classes: Authoritative confirmation admission plus shared anonymous and
            authentication scopes.

    Members:
        post: Validate and apply one username reset.
    """

    authentication_classes: tuple[type, ...] = ()
    permission_classes = (AllowAny,)
    throttle_classes = (
        UsernameResetConfirmThrottle,
        AnonymousApiThrottle,
        AuthenticationRecoveryThrottle,
    )

    @extend_schema(
        operation_id="user_username_reset_confirm",
        summary="Confirm a username reset",
        description=(
            "Accepts the account key and token from the email with a replacement username. Success "
            "consumes every outstanding username-reset link and sends a notification without "
            "revoking DRF tokens, JSON web tokens, or Django sessions issued before the change."
        ),
        request=UsernameResetConfirmSerializer,
        auth=[],
        responses={
            HTTPStatus.NO_CONTENT: OpenApiResponse(
                response=None,
                description=(
                    "The username changed, all username-reset links were consumed, existing "
                    "credentials remained valid, and a notification was scheduled."
                ),
            ),
            HTTPStatus.BAD_REQUEST: OpenApiResponse(
                response=ErrorEnvelopeSerializer,
                description=(
                    "The body or replacement username is invalid or unavailable, or the reset "
                    "token "
                    "is malformed, foreign, expired, or already used."
                ),
                examples=[
                    error_example("Malformed JSON", PARSE_ERROR),
                    error_example(
                        "Username format failure",
                        VALIDATION_ERROR,
                        details={
                            "new_username": [
                                (
                                    "Enter a valid username. This value may contain only letters, "
                                    "numbers, and @/./+/-/_ characters."
                                )
                            ]
                        },
                    ),
                    error_example(
                        "Username unavailable",
                        VALIDATION_ERROR,
                        details={"new_username": [USERNAME_UNAVAILABLE_MESSAGE]},
                    ),
                    error_example("Reset token expired", USERNAME_RESET_TOKEN_EXPIRED),
                    error_example(
                        "Reset token belongs to another account",
                        USERNAME_RESET_TOKEN_FOREIGN,
                    ),
                    error_example("Reset token malformed", USERNAME_RESET_TOKEN_MALFORMED),
                    error_example("Reset token already used", USERNAME_RESET_TOKEN_USED),
                ],
            ),
            HTTPStatus.METHOD_NOT_ALLOWED: error_response(
                "Username reset confirmation accepts only POST.",
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
                "Authoritative account, token, uniqueness, or admission state is unavailable.",
                "Service unavailable",
                SERVICE_UNAVAILABLE,
            ),
        },
    )
    def post(self, request: Request) -> Response:
        """Validate and apply one username-reset confirmation.

        Reuses the pre-admission validated body, then performs token classification, locking,
        uniqueness, replacement, and notification scheduling through the authoritative helper.

        Arguments:
            request: REST request carrying the complete reset confirmation.

        Returns:
            Empty successful response.

        Raises:
            APIException: If validation, token classification, admission, or persistence fails.
        """
        validated = validated_username_reset_confirm_data(request)
        confirm_username_reset(validated)
        return Response(status=HTTPStatus.NO_CONTENT)


class UsernameChangeSerializer(StrictFieldsSerializer):
    """Validate one authenticated username replacement body.

    Inherits from ``StrictFieldsSerializer`` and accepts only the current password and replacement
    username while keeping the password write-only.

    Attributes:
        current_password: Existing raw credential.
        new_username: Replacement login identifier.

    Members:
        None beyond field validation.
    """

    current_password = CharField(trim_whitespace=False, write_only=True)
    new_username = CharField(
        max_length=150,
        trim_whitespace=False,
        validators=(username_validator,),
    )


def change_username(account_id: object, validated_data: dict[str, Any]) -> None:
    """Replace one authenticated account username under a primary row lock.

    Rechecks the current credential against an accepted stored profile, enforces PostgreSQL
    case-insensitive uniqueness, consumes recovery links, and preserves every existing identity.

    Arguments:
        account_id: Immutable key resolved by request authentication.
        validated_data: Current password and replacement username fields.

    Returns:
        None.

    Raises:
        AuthenticationFailed: If the account disappeared before locking.
        ServiceUnavailable: If authoritative state cannot be read or updated.
        ValidationError: If the current password is incorrect or username is unavailable.
    """
    current_password = cast("str", validated_data["current_password"])
    new_username = cast("str", validated_data["new_username"])
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
            _save_username(account, new_username)
            consume_outstanding_username_reset_tokens(account)
            transaction.on_commit(
                partial(dispatch_username_change_notification, account.email),
                using="default",
            )
    except DatabaseError as error:
        raise ServiceUnavailable from error


class UsernameChangeView(APIView):
    """Replace the authenticated caller's username without revoking identities.

    Inherits from ``APIView`` and requires central authentication plus the current password while
    deliberately preserving all DRF tokens, JSON web tokens, and Django sessions.

    Attributes:
        permission_classes: Authenticated callers only.
        throttle_classes: Shared authentication and account-security scope.

    Members:
        post: Validate and replace the caller's username.
    """

    permission_classes = (IsAuthenticated,)
    throttle_classes = (AuthenticationRecoveryThrottle,)

    @extend_schema(
        operation_id="user_username_change",
        summary="Change the caller's username",
        description=(
            "Requires the current password and a format-valid, case-insensitively available "
            "username. Unlike password changes, success does not invalidate any DRF token, JSON "
            "web token, or Django session issued before the change."
        ),
        request=UsernameChangeSerializer,
        responses={
            HTTPStatus.NO_CONTENT: OpenApiResponse(
                response=None,
                description=(
                    "The username changed, existing credentials remained valid, outstanding "
                    "username-reset links were consumed, and a notification was scheduled."
                ),
            ),
            HTTPStatus.BAD_REQUEST: OpenApiResponse(
                response=ErrorEnvelopeSerializer,
                description=(
                    "The body is malformed, misses a field, carries another field, supplies an "
                    "incorrect current password, or uses an invalid or unavailable username."
                ),
                examples=[
                    error_example("Malformed JSON", PARSE_ERROR),
                    error_example(
                        "Incorrect current password",
                        VALIDATION_ERROR,
                        details={"current_password": ["The current password is incorrect."]},
                    ),
                    error_example(
                        "Username format failure",
                        VALIDATION_ERROR,
                        details={
                            "new_username": [
                                (
                                    "Enter a valid username. This value may contain only letters, "
                                    "numbers, and @/./+/-/_ characters."
                                )
                            ]
                        },
                    ),
                    error_example(
                        "Username unavailable",
                        VALIDATION_ERROR,
                        details={"new_username": [USERNAME_UNAVAILABLE_MESSAGE]},
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
                "Username change accepts only POST.",
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
                "Authoritative account, credential, or uniqueness state is unavailable.",
                "Service unavailable",
                SERVICE_UNAVAILABLE,
            ),
        },
    )
    def post(self, request: Request) -> Response:
        """Validate and replace the authenticated caller's username.

        Uses the account identity already authenticated for this request, then performs all
        credential-sensitive checks and writes against a freshly locked primary row.

        Arguments:
            request: Authenticated REST request carrying current password and new username.

        Returns:
            Empty successful response.

        Raises:
            AuthenticationFailed: If the account disappeared before locking.
            ServiceUnavailable: If authoritative state is unavailable.
            ValidationError: If a submitted field fails validation.
        """
        serializer = UsernameChangeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        change_username(cast("User", request.user).pk, serializer.validated_data)
        return Response(status=HTTPStatus.NO_CONTENT)
