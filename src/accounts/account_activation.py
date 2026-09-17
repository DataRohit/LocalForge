"""Account activation issuance and confirmation.

Creates signed, expiring, single-use activation links inside account transactions and confirms
them against digest-only database state without disclosing whether a submitted account exists.
"""

# mypy: disable-error-code=misc

import logging
import secrets
from collections.abc import Mapping
from functools import partial
from hashlib import sha256
from typing import TYPE_CHECKING, Any, cast, override

from celery.exceptions import CeleryError
from django.conf import settings
from django.core import signing
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import validate_email
from django.db import DatabaseError, connections, transaction
from django.db.models import Value
from django.db.models.functions import Lower
from django.utils import timezone
from kombu.exceptions import KombuError
from rest_framework.exceptions import ValidationError
from rest_framework.serializers import CharField, EmailField, Serializer, UUIDField
from rest_framework.throttling import BaseThrottle

from accounts.activation_tokens import (
    activation_token_digest,
    activation_token_remaining_seconds,
    create_activation_token,
    load_activation_payload,
)
from accounts.login_throttle import PostgresLoginThrottleStore, RollingWindowRule
from accounts.models import ActivationToken, User
from accounts.normalisation import normalise_email
from accounts.tasks import send_activation_email
from accounts.token_authentication import parse_throttle_rate, trusted_client_address
from config.api_errors import (
    ActivationTokenExpired,
    ActivationTokenForeign,
    ActivationTokenMalformed,
    ActivationTokenUsed,
    ServiceUnavailable,
)
from config.logs import REQUEST_ID_META_KEY

if TYPE_CHECKING:
    import uuid
    from datetime import datetime

    from rest_framework.request import Request
    from rest_framework.views import APIView

logger = logging.getLogger(__name__)

RESEND_ACCEPTED_MESSAGE = (
    "If an inactive account matches this address, an activation email will be sent."
)
RESEND_VALIDATED_DATA_ATTRIBUTE = "_localforge_activation_resend_validated_data"


class StrictActivationSerializer(Serializer):
    """Reject fields outside an activation request contract.

    Inherits from DRF's ``Serializer`` and turns every undeclared key into field detail instead of
    silently ignoring it, keeping activation inputs explicit.

    Attributes:
        None beyond those inherited from ``Serializer``.

    Members:
        to_internal_value: Reject undeclared keys before normal field validation.
    """

    @override
    def to_internal_value(self, data: object) -> dict[str, Any]:
        """Reject undeclared keys before validating declared fields.

        Preserves DRF's normal non-object handling and returns one validation entry for every extra
        key in a submitted mapping.

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


class ActivationConfirmationSerializer(StrictActivationSerializer):
    """Validate one activation confirmation request.

    Inherits from ``StrictActivationSerializer`` and accepts only the public account key carried in
    the link and its signed one-time token.

    Attributes:
        account: Account key copied from the activation link.
        token: Signed one-time activation value.

    Members:
        None beyond those inherited from ``Serializer``.
    """

    account = UUIDField()
    token = CharField(trim_whitespace=False)


class ActivationResendSerializer(StrictActivationSerializer):
    """Validate one activation resend request.

    Inherits from ``StrictActivationSerializer`` and accepts only an email address, canonicalizing
    it to the same form account persistence uses before any lookup or throttle identity is derived.

    Attributes:
        email: Address that may belong to an inactive account.

    Members:
        validate_email: Canonicalize and revalidate the submitted address.
    """

    email = EmailField(max_length=254)

    def validate_email(self, value: str) -> str:
        """Canonicalize the submitted resend address.

        Applies storage normalization and validates the result again so Unicode case conversion
        cannot turn accepted input into a different invalid database lookup.

        Arguments:
            value: Syntactically valid submitted address.

        Returns:
            Valid normalized email address.

        Raises:
            ValidationError: If normalization produces an invalid address.
        """
        normalized = normalise_email(value)
        try:
            validate_email(normalized)
        except DjangoValidationError as error:
            raise ValidationError(error.messages) from error
        return normalized


def _dispatch_activation_email(account_id: str, token: str) -> None:
    """Enqueue one activation email after commit.

    Contains broker publication failures so an account transaction that already committed cannot
    turn into a server error or expose recipient and token values through exception text.

    Arguments:
        account_id: Account key carried by the activation link.
        token: Signed one-time activation value.

    Returns:
        None.
    """
    try:
        remaining_seconds = activation_token_remaining_seconds(token)
    except signing.BadSignature, TypeError, ValueError:
        return
    if remaining_seconds == 0:
        return

    try:
        send_activation_email.apply_async(
            args=(account_id, token),
            expires=remaining_seconds,
        )
    except (CeleryError, KombuError, OSError) as error:
        error_type = type(error).__name__
    else:
        return

    logger.error(
        "Activation email publication failed",
        extra={"task_error_type": error_type},
    )


def issue_activation(account: User) -> None:
    """Issue one signed activation token inside the current transaction.

    Persists only the digest and registers task publication with ``on_commit``, ensuring a rollback
    removes both the account state and the pending email side effect.

    Arguments:
        account: Inactive account that should receive a fresh link.

    Returns:
        None.

    Raises:
        ServiceUnavailable: If token persistence fails.
    """
    token = create_activation_token(str(account.pk))
    try:
        ActivationToken.objects.using("default").create(
            account=account,
            subject_id=account.pk,
            digest=activation_token_digest(token),
        )
    except DatabaseError as error:
        raise ServiceUnavailable from error

    transaction.on_commit(
        partial(_dispatch_activation_email, str(account.pk), token),
        using="default",
    )


def _dummy_activation() -> tuple[str, str]:
    """Build a signed activation-shaped value that belongs to no account.

    Gives active and unknown resend outcomes the same signing and task-publication path as an
    inactive account while the task's authoritative lookup guarantees no email is sent.

    Arguments:
        None.

    Returns:
        Random account key and signed token carrying it.
    """
    account_id = str(secrets.token_hex(16))
    token = create_activation_token(account_id)
    return account_id, token


def schedule_dummy_activation() -> None:
    """Schedule an activation-shaped task that cannot send email.

    Gives active and unknown accepted outcomes the same signed payload and queue-publication work as
    a real activation while omitting any recipient or authoritative digest record.

    Arguments:
        None.

    Returns:
        None.
    """
    account_id, token = _dummy_activation()
    transaction.on_commit(
        partial(_dispatch_activation_email, account_id, token),
        using="default",
    )


def resolve_activation_account(email: str) -> User | None:
    """Resolve one email with the model constraint's PostgreSQL semantics.

    Runs on the authoritative primary and compares database lowercase expressions, matching the
    functional uniqueness constraint rather than a runtime-specific Unicode transformation.

    Arguments:
        email: Normalized address submitted to activation resend.

    Returns:
        Matching account, or None when no account exists.

    Raises:
        DatabaseError: If the authoritative primary cannot resolve identity.
    """
    try:
        return (
            User.objects.using("default")
            .select_for_update()
            .alias(activation_email=Lower("email"))
            .get(activation_email=Lower(Value(email)))
        )
    except User.DoesNotExist:
        return None


def activation_email_identity(email: str) -> str:
    """Build one stable normalized-email admission identity.

    Hashes PostgreSQL's lowercase result independently of account existence, so admissions made
    before registration remain authoritative after an account is created.

    Arguments:
        email: Normalized address submitted to activation resend.

    Returns:
        Opaque normalized-email identity.

    Raises:
        DatabaseError: If the authoritative primary cannot normalize identity.
    """
    with connections["default"].cursor() as cursor:
        cursor.execute("SELECT LOWER(%s)", [email])
        normalized = cast("tuple[str]", cursor.fetchone())[0]

    return sha256(f"email:{normalized}".encode()).hexdigest()


def _activation_mail_rules(
    email: str,
    account: User | None,
) -> tuple[RollingWindowRule, ...]:
    """Build authoritative activation-mail admission dimensions.

    Always includes the stable normalized-email bucket and adds the immutable account key when the
    address currently resolves, using one configured limit for both recipient identities.

    Arguments:
        email: Valid normalized activation email address.
        account: Primary-resolved account, or None when the address is unknown.

    Returns:
        Email and optional account rolling-window rules.

    Raises:
        DatabaseError: If PostgreSQL cannot normalize the email identity.
        ValueError: If the configured rate is invalid.
    """
    limit, window_seconds = parse_throttle_rate(
        cast("str", settings.ACCOUNT_ACTIVATION_RESEND_ACCOUNT_THROTTLE_RATE)
    )
    rules = [
        RollingWindowRule(
            key=f"activation-mail-email:{activation_email_identity(email)}",
            limit=limit,
            window_seconds=window_seconds,
        )
    ]
    if account is not None:
        account_identity = sha256(f"account:{account.pk}".encode()).hexdigest()
        rules.append(
            RollingWindowRule(
                key=f"activation-mail-account:{account_identity}",
                limit=limit,
                window_seconds=window_seconds,
            )
        )
    return tuple(rules)


def admit_duplicate_activation(account: User, email: str) -> bool:
    """Admit duplicate-registration activation mail under resend identities.

    Requires the caller to hold the primary account row lock, then atomically charges the stable
    email and immutable account buckets in the same sorted advisory-lock decision as resend.

    Arguments:
        account: Existing duplicate account locked on the primary database.
        email: Valid normalized submitted email.

    Returns:
        Whether a real activation email may be issued.

    Raises:
        DatabaseError: If authoritative admission is unavailable.
        ValueError: If the configured rate is invalid.
    """
    decision = PostgresLoginThrottleStore(settings.LOGIN_THROTTLE_DATABASE_ALIAS).admit(
        _activation_mail_rules(email, account),
        member=f"duplicate-registration:{secrets.token_hex(16)}",
    )
    return decision.admitted


def validated_activation_resend_data(request: Request) -> dict[str, Any]:
    """Validate and cache one complete activation-resend body.

    Enforces the exact public serializer contract before admission and retains its normalized
    result on the DRF request so the view schedules from the same validation result.

    Arguments:
        request: Resend request being validated before admission or scheduling.

    Returns:
        Complete validated native field mapping.

    Raises:
        ValidationError: If the parsed body is not exactly one valid email field.
    """
    cached = getattr(request, RESEND_VALIDATED_DATA_ATTRIBUTE, None)
    if isinstance(cached, dict):
        return cast("dict[str, Any]", cached)

    serializer = ActivationResendSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    validated_data = cast("dict[str, Any]", serializer.validated_data)
    setattr(request, RESEND_VALIDATED_DATA_ATTRIBUTE, validated_data)
    return validated_data


class ActivationResendThrottle(BaseThrottle):
    """Enforce address and account resend limits atomically.

    Inherits from DRF's ``BaseThrottle`` and writes both dimensions beside account state on the
    primary, so eviction, restart, and multiple application processes cannot reset the boundary.

    Attributes:
        retry_after_seconds: Authoritative delay after a rejected request.

    Members:
        allow_request: Atomically admit or reject both resend dimensions.
        wait: Return the primary-derived retry delay.
    """

    retry_after_seconds: int | None = None

    @override
    def allow_request(self, request: Request, view: APIView) -> bool:
        """Atomically enforce every usable resend dimension.

        Always limits the client address and adds the account identity for a valid submitted email,
        failing closed when PostgreSQL cannot make the shared decision.

        Arguments:
            request: Resend request being admitted.
            view: Resend view applying the throttle.

        Returns:
            Whether authoritative shared state admitted the request.

        Raises:
            ServiceUnavailable: If authoritative throttle state is unavailable.
            ValueError: If a configured rate is invalid.
        """
        del view
        if request.method != "POST":
            return True

        email = cast("str", validated_activation_resend_data(request)["email"])

        address_limit, address_window = parse_throttle_rate(
            cast("str", settings.ACCOUNT_ACTIVATION_RESEND_ADDRESS_THROTTLE_RATE)
        )
        address = sha256(trusted_client_address(request).encode()).hexdigest()
        rules = [
            RollingWindowRule(
                key=f"activation-resend-address:{address}",
                limit=address_limit,
                window_seconds=address_window,
            )
        ]
        try:
            with transaction.atomic(using="default"):
                account = resolve_activation_account(email)
                rules.extend(_activation_mail_rules(email, account))
                request_id = request.META.get(REQUEST_ID_META_KEY)
                correlation = request_id if isinstance(request_id, str) else "uncorrelated"
                member = f"{correlation}:{secrets.token_hex(16)}"
                decision = PostgresLoginThrottleStore(settings.LOGIN_THROTTLE_DATABASE_ALIAS).admit(
                    tuple(rules),
                    member=member,
                )
        except DatabaseError as error:
            raise ServiceUnavailable from error

        self.retry_after_seconds = decision.retry_after_seconds
        return decision.admitted

    @override
    def wait(self) -> float | None:
        """Return the authoritative resend retry delay.

        Supplies DRF with the exact primary-derived duration for ``Retry-After`` and returns no
        estimate before a denied decision has populated the value.

        Arguments:
            None.

        Returns:
            Retry delay in seconds, or None before a rejection.
        """
        return float(self.retry_after_seconds) if self.retry_after_seconds is not None else None


def request_activation_resend(email: str) -> None:
    """Schedule one indistinguishable resend outcome.

    Issues a real token only for an inactive account and otherwise publishes an activation-shaped
    dummy task that authoritative token lookup discards without sending mail. A real token write
    failure rolls back to its savepoint and follows the same dummy path.

    Arguments:
        email: Valid normalized address submitted by the caller.

    Returns:
        None.

    Raises:
        ServiceUnavailable: If authoritative lookup state is unavailable.
    """
    try:
        with transaction.atomic(using="default"):
            account = resolve_activation_account(email)
            if account is not None and not account.is_active:
                try:
                    with transaction.atomic(using="default"):
                        issue_activation(account)
                except DatabaseError, ServiceUnavailable:
                    schedule_dummy_activation()
                return

            schedule_dummy_activation()
    except DatabaseError as error:
        raise ServiceUnavailable from error


def save_account_from_admin(account: User) -> None:
    """Persist one existing account through the supported administration seam.

    Locks the authoritative account before saving submitted administration fields. While the
    persisted account is inactive, activation or a normalized email change consumes every
    outstanding activation record before the replacement state is saved.

    Arguments:
        account: Existing account carrying validated administration form values.

    Returns:
        None.

    Raises:
        DatabaseError: If authoritative account or token state cannot be persisted.
        User.DoesNotExist: If the edited account no longer exists.
    """
    with transaction.atomic(using="default"):
        persisted = User.objects.using("default").select_for_update().get(pk=account.pk)
        account.email = normalise_email(account.email)
        activating = not persisted.is_active and account.is_active
        email_changing = not persisted.is_active and persisted.email != account.email
        if activating or email_changing:
            _consume_outstanding_activation_tokens(persisted, timezone.now())
        account.save(using="default")


def confirm_activation(validated_data: dict[str, Any]) -> None:
    """Activate the account bound to one valid unused token.

    Authenticates and age-checks the signed payload before locking its digest record and account,
    then consumes every outstanding token in the same transaction that activates the account.

    Arguments:
        validated_data: Validated account key and signed token.

    Returns:
        None.

    Raises:
        ActivationTokenExpired: If the signed timestamp exceeded the configured lifetime.
        ActivationTokenForeign: If the token is not bound to the submitted account.
        ActivationTokenMalformed: If the signed value cannot be authenticated or decoded.
        ActivationTokenUsed: If an earlier confirmation consumed the token.
        ServiceUnavailable: If authoritative account or token state is unavailable.
    """
    account_key = cast("uuid.UUID", validated_data["account"])
    account_id = str(account_key)
    token = cast("str", validated_data["token"])
    try:
        payload = load_activation_payload(token)
    except signing.SignatureExpired as error:
        raise ActivationTokenExpired from error
    except (signing.BadSignature, TypeError, ValueError) as error:
        raise ActivationTokenMalformed from error

    if payload is None or payload[0] != account_id:
        raise ActivationTokenForeign

    digest = activation_token_digest(token)
    try:
        _activate_token_record(account_key, digest)
    except DatabaseError as error:
        raise ServiceUnavailable from error


def _activate_token_record(account_id: uuid.UUID, digest: str) -> None:
    """Consume one authoritative activation record.

    Locks the account before its token so concurrent links for one account serialize in one order,
    then activates and consumes every outstanding link atomically.

    Arguments:
        account_id: Submitted account key authenticated by the signed payload.
        digest: Digest of the submitted signed token.

    Returns:
        None.

    Raises:
        ActivationTokenForeign: If the account or digest record does not match.
        ActivationTokenUsed: If activation already consumed the token or account.
        DatabaseError: If authoritative state cannot be read or written.
    """
    preflight = (
        ActivationToken.objects.using("default")
        .filter(digest=digest, subject_id=account_id)
        .values_list("pk", flat=True)
        .first()
    )
    if preflight is None:
        raise ActivationTokenForeign

    with transaction.atomic(using="default"):
        try:
            account = User.objects.using("default").select_for_update().get(pk=account_id)
        except User.DoesNotExist as error:
            record = (
                ActivationToken.objects.using("default")
                .select_for_update()
                .filter(pk=preflight, digest=digest, subject_id=account_id)
                .first()
            )
            if record is not None and record.used_at is not None:
                raise ActivationTokenUsed from error
            raise ActivationTokenForeign from error
        record = (
            ActivationToken.objects.using("default")
            .select_for_update()
            .filter(
                pk=preflight,
                digest=digest,
                subject_id=account_id,
                account=account,
            )
            .first()
        )
        if record is None:
            raise ActivationTokenForeign
        if record.used_at is not None or account.is_active:
            raise ActivationTokenUsed

        used_at = timezone.now()
        account.is_active = True
        account.save(using="default", update_fields=("is_active", "updated_at"))
        _consume_outstanding_activation_tokens(account, used_at)


def _consume_outstanding_activation_tokens(account: User, used_at: datetime) -> None:
    """Consume every outstanding activation record for one locked account.

    Updates token rows only after the caller has acquired the primary account lock, preserving
    account-before-token lock order across confirmation, delivery, resend, and administration.

    Arguments:
        account: Primary account whose row is already locked by the caller.
        used_at: Authoritative timestamp applied to every outstanding record.

    Returns:
        None.
    """
    ActivationToken.objects.using("default").filter(
        account=account,
        used_at__isnull=True,
    ).update(used_at=used_at)
