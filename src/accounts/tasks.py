"""Account email background tasks.

Registers account-message delivery with the project Celery application so web requests enqueue
mail work while testing can retain the repository's eager task seam.
"""

import logging
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast
from urllib.parse import urlencode

from django.core import signing
from django.db import transaction
from django.utils import timezone

from accounts.activation_tokens import activation_token_digest, load_activation_payload
from accounts.models import ActivationToken, PasswordResetToken, User, UsernameResetToken
from accounts.password_tokens import (
    password_reset_token_digest,
    password_reset_token_is_expired,
    password_reset_token_matches,
)
from accounts.username_tokens import (
    username_reset_token_digest,
    username_reset_token_is_expired,
    username_reset_token_matches,
)
from config.celery import app
from config.email import send_application_email
from notifications.delivery import publish_notification

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from typing import Protocol

    from celery import Celery
    from celery.result import AsyncResult, EagerResult

    class ActivationEmailTask(Protocol):
        """Callable activation task interface.

        Inherits from ``Protocol`` and exposes the direct and delayed call shapes consumed by
        application code and tests without depending on Celery's untyped decorator result.

        Attributes:
            app: Celery application carrying effective acknowledgement settings.
            autoretry_for: Exception classes this task retries automatically.

        Members:
            __call__: Execute activation delivery in the current process.
            apply_async: Publish activation delivery through Celery with expiry.
        """

        app: Celery
        autoretry_for: tuple[type[BaseException], ...]

        def __call__(self, account_id: str, token: str) -> bool:
            """Execute one activation delivery.

            Resolves authoritative token state before rendering, matching the behavior a worker
            executes when Celery invokes the task object.

            Arguments:
                account_id: Account key carried by the activation link.
                token: Signed one-time activation value.

            Returns:
                Whether the configured email backend accepted the message.
            """
            ...

        def apply_async(
            self,
            args: tuple[str, str],
            *,
            expires: int,
        ) -> AsyncResult:
            """Publish one expiring activation delivery task.

            Exposes the queue boundary and broker expiry used by after-commit callbacks while
            retaining the concrete types Celery's decorator does not publish.

            Arguments:
                args: Account key and signed one-time activation value.
                expires: Remaining token lifetime in seconds.

            Returns:
                Celery result handle for the queued delivery.
            """
            ...

    class PasswordResetEmailTask(Protocol):
        """Callable password-reset delivery task interface.

        Inherits from ``Protocol`` and exposes direct and delayed execution without depending on
        Celery's dynamically generated task type.

        Attributes:
            app: Celery application carrying acknowledgement settings.
            autoretry_for: Exception classes retried automatically.

        Members:
            __call__: Execute reset delivery in the current process.
            apply_async: Publish reset delivery with broker expiry.
        """

        app: Celery
        autoretry_for: tuple[type[BaseException], ...]

        def __call__(self, account_id: str, token: str) -> bool:
            """Execute one password-reset delivery.

            Resolves and claims authoritative reset state before rendering, matching the behavior
            Celery invokes in a worker or the eager testing seam.

            Arguments:
                account_id: Immutable account key rendered into the reset link.
                token: Django password-reset bearer value.

            Returns:
                Whether the configured email backend accepted the message.
            """
            ...

        def apply_async(
            self,
            args: tuple[str, str],
            *,
            expires: int,
        ) -> AsyncResult:
            """Publish one expiring password-reset task.

            Exposes the queue boundary and broker expiry used by after-commit callbacks while
            retaining the concrete types Celery's decorator does not publish.

            Arguments:
                args: Immutable account key and bearer value.
                expires: Remaining confirmation lifetime.

            Returns:
                Celery result handle for the queued delivery.
            """
            ...

    class UsernameResetEmailTask(Protocol):
        """Callable username-reset delivery task interface.

        Inherits from ``Protocol`` and exposes direct and delayed execution without depending on
        Celery's dynamically generated task type.

        Attributes:
            app: Celery application carrying effective acknowledgement settings.
            autoretry_for: Exception classes retried automatically.

        Members:
            __call__: Execute username-reset delivery in the current process.
            apply_async: Publish username-reset delivery with broker expiry.
        """

        app: Celery
        autoretry_for: tuple[type[BaseException], ...]

        def __call__(self, account_id: str, token: str) -> bool:
            """Execute one username-reset delivery.

            Resolves and claims authoritative reset state before rendering, matching the behavior
            Celery invokes in a worker or the eager testing seam.

            Arguments:
                account_id: Immutable account key rendered into the reset link.
                token: Django username-reset bearer value.

            Returns:
                Whether the configured email backend accepted the message.
            """
            ...

        def apply_async(
            self,
            args: tuple[str, str],
            *,
            expires: int,
        ) -> AsyncResult:
            """Publish one expiring username-reset task.

            Exposes the queue boundary and broker expiry used by after-commit callbacks while
            retaining the concrete types Celery's decorator does not publish.

            Arguments:
                args: Immutable account key and bearer value.
                expires: Remaining confirmation lifetime.

            Returns:
                Celery result handle for the queued delivery.
            """
            ...

    class NotificationEmailTask(Protocol):
        """Callable credential-free notification task interface.

        Inherits from ``Protocol`` and exposes eager plus queued execution using only an immutable
        account identifier.

        Attributes:
            app: Celery application carrying acknowledgement and retry settings.
            autoretry_for: Exception classes retried automatically.
            max_retries: Retries permitted after the initial attempt.
            name: Registered task name.

        Members:
            __call__: Execute notification delivery in the current process.
            apply: Execute through Celery's eager task lifecycle.
            apply_async: Publish notification delivery.
        """

        app: Celery
        autoretry_for: tuple[type[BaseException], ...]
        max_retries: int
        name: str

        def __call__(self, account_id: str) -> bool:
            """Execute one account notification.

            Resolves current primary account state before crossing the mail boundary, matching the
            deployed worker and eager testing behavior.

            Arguments:
                account_id: Immutable account identifier used to load the current recipient.

            Returns:
                Whether delivery completed or no live account remained.
            """
            ...

        def apply(
            self,
            args: tuple[str],
            *,
            throw: bool,
        ) -> EagerResult:
            """Execute one notification through Celery's eager lifecycle.

            Runs retry and failure hooks in-process so tests can prove bounded transient and
            permanent outcomes without a separate worker.

            Arguments:
                args: One immutable account identifier.
                throw: Whether terminal failure should escape rather than be returned.

            Returns:
                Eager result carrying success or terminal failure state.
            """
            ...

        def apply_async(
            self,
            args: tuple[str],
        ) -> AsyncResult:
            """Publish one credential-free notification.

            Sends only the immutable identifier through the broker and leaves recipient resolution
            to worker execution.

            Arguments:
                args: One immutable account identifier.

            Returns:
                Celery result handle for the queued notification.
            """
            ...


@dataclass(frozen=True, slots=True)
class ActivationDeliveryClaim:
    """Durable authority to attempt one activation delivery.

    Inherits from ``dataclass`` and carries only locked authoritative identifiers needed to
    revalidate delivery after the claim commits.

    Attributes:
        subject_id: Immutable account subject carried by the token.
        digest: Digest of the signed activation token.
        record_id: Claimed activation-token record key.

    Members:
        None.
    """

    subject_id: uuid.UUID
    digest: str
    record_id: int


def _claim_activation_delivery(
    subject_id: uuid.UUID,
    digest: str,
) -> ActivationDeliveryClaim | None:
    """Claim one activation delivery durably.

    Locks the primary account before its token, rechecks active and use state, and commits a claim
    before any SMTP side effect so worker loss or redelivery cannot send the bearer twice.

    Arguments:
        subject_id: Immutable account subject carried by the activation link.
        digest: Digest of the signed activation token.

    Returns:
        Token record key and recipient for the claimed delivery, or None when it cannot send.
    """
    with transaction.atomic(using="default"):
        try:
            account = User.objects.using("default").select_for_update().get(pk=subject_id)
        except User.DoesNotExist:
            return None
        record = (
            ActivationToken.objects.using("default")
            .select_for_update()
            .filter(
                account=account,
                subject_id=subject_id,
                digest=digest,
                used_at__isnull=True,
                delivery_claimed_at__isnull=True,
            )
            .first()
        )
        if record is None or account.is_active:
            return None
        record.delivery_claimed_at = timezone.now()
        record.save(using="default", update_fields=("delivery_claimed_at",))
        return ActivationDeliveryClaim(
            subject_id=subject_id,
            digest=digest,
            record_id=record.pk,
        )


def _send_claimed_activation(
    claim: ActivationDeliveryClaim,
    account_id: str,
    token: str,
) -> bool:
    """Send one durably claimed activation delivery.

    Reacquires the primary account before the token and holds both through SMTP, preventing a
    concurrent activation from turning the claimed message stale between state validation and send.

    Arguments:
        claim: Durable subject, digest, and record authority.
        account_id: Public account key rendered into the activation link.
        token: Signed one-time activation value rendered into the link.

    Returns:
        Whether the configured email backend accepted the message.
    """
    with transaction.atomic(using="default"):
        try:
            account = User.objects.using("default").select_for_update().get(pk=claim.subject_id)
        except User.DoesNotExist:
            return False
        record = (
            ActivationToken.objects.using("default")
            .select_for_update()
            .filter(
                pk=claim.record_id,
                account=account,
                subject_id=claim.subject_id,
                digest=claim.digest,
                used_at__isnull=True,
                delivery_claimed_at__isnull=False,
                delivered_at__isnull=True,
            )
            .first()
        )
        if record is None or account.is_active:
            return False

        try:
            load_activation_payload(token)
        except (
            signing.BadSignature,
            TypeError,
            ValueError,
        ):
            return False

        recipient = account.email
        action_path = f"/users/?{urlencode({'account': account_id, 'token': token})}"
        delivered = send_application_email(
            recipient,
            "Activate your LocalForge account",
            "Confirm your email address to activate your account.",
            action_path=action_path,
        )
        if delivered:
            record.delivered_at = timezone.now()
            record.save(using="default", update_fields=("delivered_at",))
        return delivered


def deliver_activation_email(account_id: str, token: str) -> bool:
    """Deliver one multipart activation email at most once.

    Validates the signed bearer with confirmation semantics, commits a durable delivery claim, and
    sends only while locked authoritative account and token state still permit activation.

    Arguments:
        account_id: Account key carried by the activation link.
        token: Signed one-time activation value.

    Returns:
        Whether the configured email backend accepted the message.
    """
    try:
        payload = load_activation_payload(token)
        subject_id = uuid.UUID(account_id)
    except (
        signing.BadSignature,
        TypeError,
        ValueError,
    ):
        return False
    if payload is None or payload[0] != account_id:
        return False

    digest = activation_token_digest(token)
    claim = _claim_activation_delivery(subject_id, digest)
    if claim is None:
        return False
    return _send_claimed_activation(
        claim,
        account_id,
        token,
    )


send_activation_email = cast(
    "ActivationEmailTask",
    app.task(
        name="accounts.send_activation_email",
        autoretry_for=(),
    )(deliver_activation_email),
)


@dataclass(frozen=True, slots=True)
class PasswordResetDeliveryClaim:
    """Durable authority to attempt one password-reset delivery.

    Inherits from ``dataclass`` and carries only the locked identifiers needed to revalidate one
    claimed SMTP attempt after its claim commits.

    Attributes:
        subject_id: Immutable account subject carried by the link.
        digest: Digest of the password-reset bearer.
        record_id: Claimed password-reset record key.

    Members:
        None.
    """

    subject_id: uuid.UUID
    digest: str
    record_id: int


def _claim_password_reset_delivery(
    subject_id: uuid.UUID,
    digest: str,
    token: str,
) -> PasswordResetDeliveryClaim | None:
    """Claim one password-reset delivery durably.

    Locks the primary account before its reset record, revalidates account-bound token state, and
    commits a claim before SMTP so redelivery cannot repeat the side effect.

    Arguments:
        subject_id: Immutable account subject carried by the link.
        digest: Digest of the password-reset bearer.
        token: Password-reset bearer value.

    Returns:
        Durable delivery claim, or None when delivery is no longer authorized.
    """
    with transaction.atomic(using="default"):
        try:
            account = User.objects.using("default").select_for_update().get(pk=subject_id)
        except User.DoesNotExist:
            return None
        record = (
            PasswordResetToken.objects.using("default")
            .select_for_update()
            .filter(
                account=account,
                subject_id=subject_id,
                digest=digest,
                used_at__isnull=True,
                delivery_claimed_at__isnull=True,
            )
            .first()
        )
        if (
            record is None
            or not account.is_active
            or password_reset_token_is_expired(token)
            or not password_reset_token_matches(account, token)
        ):
            return None
        record.delivery_claimed_at = timezone.now()
        record.save(using="default", update_fields=("delivery_claimed_at",))
        return PasswordResetDeliveryClaim(
            subject_id=subject_id,
            digest=digest,
            record_id=record.pk,
        )


def _send_claimed_password_reset(
    claim: PasswordResetDeliveryClaim,
    account_id: str,
    token: str,
) -> bool:
    """Send one durably claimed password-reset message.

    Reacquires account-before-token locks and repeats expiry and state validation while holding
    them through SMTP, so deletion, email change, password change, or confirmation wins.

    Arguments:
        claim: Durable reset-delivery authority.
        account_id: Immutable account key rendered into the link.
        token: Password-reset bearer value rendered into the link.

    Returns:
        Whether the configured email backend accepted the message.
    """
    with transaction.atomic(using="default"):
        try:
            account = User.objects.using("default").select_for_update().get(pk=claim.subject_id)
        except User.DoesNotExist:
            return False
        record = (
            PasswordResetToken.objects.using("default")
            .select_for_update()
            .filter(
                pk=claim.record_id,
                account=account,
                subject_id=claim.subject_id,
                digest=claim.digest,
                used_at__isnull=True,
                delivery_claimed_at__isnull=False,
                delivered_at__isnull=True,
            )
            .first()
        )
        if (
            record is None
            or not account.is_active
            or password_reset_token_is_expired(token)
            or not password_reset_token_matches(account, token)
        ):
            return False

        action_path = (
            f"/users/reset_password_confirm/?{urlencode({'account': account_id, 'token': token})}"
        )
        delivered = send_application_email(
            account.email,
            "Reset your LocalForge password",
            "Use this link to choose a new password.",
            action_path=action_path,
        )
        if delivered:
            record.delivered_at = timezone.now()
            record.save(using="default", update_fields=("delivered_at",))
        return delivered


def deliver_password_reset_email(account_id: object, token: object) -> bool:
    """Deliver one multipart password-reset email at most once.

    Rejects non-string payloads plus malformed, expired, mismatched, changed, deleted, used, or
    redelivered state before acquiring and consuming the sole durable SMTP claim.

    Arguments:
        account_id: Immutable account key carried by the reset link.
        token: Django password-reset bearer value.

    Returns:
        Whether the configured email backend accepted the message.
    """
    if not isinstance(account_id, str) or not isinstance(token, str):
        return False
    try:
        subject_id = uuid.UUID(account_id)
        if password_reset_token_is_expired(token):
            return False
    except (
        TypeError,
        ValueError,
    ):
        return False
    digest = password_reset_token_digest(token)
    claim = _claim_password_reset_delivery(subject_id, digest, token)
    if claim is None:
        return False
    return _send_claimed_password_reset(claim, account_id, token)


send_password_reset_email = cast(
    "PasswordResetEmailTask",
    app.task(
        name="accounts.send_password_reset_email",
        autoretry_for=(),
    )(deliver_password_reset_email),
)


@dataclass(frozen=True, slots=True)
class UsernameResetDeliveryClaim:
    """Durable authority to attempt one username-reset delivery.

    Inherits from ``dataclass`` and carries only the locked identifiers needed to revalidate one
    claimed SMTP attempt after its claim commits.

    Attributes:
        subject_id: Immutable account subject carried by the link.
        digest: Digest of the username-reset bearer.
        record_id: Claimed username-reset record key.

    Members:
        None.
    """

    subject_id: uuid.UUID
    digest: str
    record_id: int


def _claim_username_reset_delivery(
    subject_id: uuid.UUID,
    digest: str,
    token: str,
) -> UsernameResetDeliveryClaim | None:
    """Claim one username-reset delivery durably.

    Locks the primary account before its reset record, revalidates account-bound token state, and
    commits a claim before SMTP so redelivery cannot repeat the side effect.

    Arguments:
        subject_id: Immutable account subject carried by the link.
        digest: Digest of the username-reset bearer.
        token: Username-reset bearer value.

    Returns:
        Durable delivery claim, or None when delivery is no longer authorized.
    """
    with transaction.atomic(using="default"):
        try:
            account = User.objects.using("default").select_for_update().get(pk=subject_id)
        except User.DoesNotExist:
            return None
        record = (
            UsernameResetToken.objects.using("default")
            .select_for_update()
            .filter(
                account=account,
                subject_id=subject_id,
                digest=digest,
                used_at__isnull=True,
                delivery_claimed_at__isnull=True,
            )
            .first()
        )
        if (
            record is None
            or not account.is_active
            or username_reset_token_is_expired(token)
            or not username_reset_token_matches(account, token)
        ):
            return None
        record.delivery_claimed_at = timezone.now()
        record.save(using="default", update_fields=("delivery_claimed_at",))
        return UsernameResetDeliveryClaim(
            subject_id=subject_id,
            digest=digest,
            record_id=record.pk,
        )


def _send_claimed_username_reset(
    claim: UsernameResetDeliveryClaim,
    account_id: str,
    token: str,
) -> bool:
    """Send one durably claimed username-reset message.

    Reacquires account-before-token locks and repeats expiry and state validation while holding
    them through SMTP, so deletion, email change, password change, or confirmation wins.

    Arguments:
        claim: Durable reset-delivery authority.
        account_id: Immutable account key rendered into the link.
        token: Username-reset bearer value rendered into the link.

    Returns:
        Whether the configured email backend accepted the message.
    """
    with transaction.atomic(using="default"):
        try:
            account = User.objects.using("default").select_for_update().get(pk=claim.subject_id)
        except User.DoesNotExist:
            return False
        record = (
            UsernameResetToken.objects.using("default")
            .select_for_update()
            .filter(
                pk=claim.record_id,
                account=account,
                subject_id=claim.subject_id,
                digest=claim.digest,
                used_at__isnull=True,
                delivery_claimed_at__isnull=False,
                delivered_at__isnull=True,
            )
            .first()
        )
        if (
            record is None
            or not account.is_active
            or username_reset_token_is_expired(token)
            or not username_reset_token_matches(account, token)
        ):
            return False

        action_path = (
            f"/users/reset_username_confirm/?{urlencode({'account': account_id, 'token': token})}"
        )
        delivered = send_application_email(
            account.email,
            "Reset your LocalForge username",
            "Use this link to choose a new username.",
            action_path=action_path,
        )
        if delivered:
            record.delivered_at = timezone.now()
            record.save(using="default", update_fields=("delivered_at",))
        return delivered


def deliver_username_reset_email(account_id: object, token: object) -> bool:
    """Deliver one multipart username-reset email at most once.

    Rejects non-string payloads plus malformed, expired, mismatched, changed, deleted, used, or
    redelivered state before acquiring and consuming the sole durable SMTP claim.

    Arguments:
        account_id: Immutable account key carried by the reset link.
        token: Django username-reset bearer value.

    Returns:
        Whether the configured email backend accepted the message.
    """
    if not isinstance(account_id, str) or not isinstance(token, str):
        return False
    try:
        subject_id = uuid.UUID(account_id)
        if username_reset_token_is_expired(token):
            return False
    except (
        TypeError,
        ValueError,
    ):
        return False
    digest = username_reset_token_digest(token)
    claim = _claim_username_reset_delivery(subject_id, digest, token)
    if claim is None:
        return False
    return _send_claimed_username_reset(claim, account_id, token)


send_username_reset_email = cast(
    "UsernameResetEmailTask",
    app.task(
        name="accounts.send_username_reset_email",
        autoretry_for=(),
    )(deliver_username_reset_email),
)


class AccountEmailDeliveryError(RuntimeError):
    """Signal that a retry-safe account notification was not accepted.

    Inherits from ``RuntimeError`` and carries no recipient or transport text, allowing the base
    task retry and dead-letter lifecycle to remain credential-free.

    Attributes:
        None.

    Members:
        None.
    """


def _notification_recipient(account_id: object) -> str | None:
    """Resolve the current recipient for one immutable account identifier.

    Reads the authoritative primary at execution time so queued work never carries an address or
    full user object and naturally suppresses delivery after account deletion.

    Arguments:
        account_id: Candidate immutable account identifier.

    Returns:
        Current email address, or None when the identifier or account is unavailable.
    """
    if not isinstance(account_id, str):
        return None
    try:
        subject_id = uuid.UUID(account_id)
        recipient = User.objects.using("default").values_list("email", flat=True).get(pk=subject_id)
    except (
        TypeError,
        ValueError,
        User.DoesNotExist,
    ):
        return None
    return str(recipient)


def deliver_password_changed_email(account_id: object) -> bool:
    """Deliver one credential-free password-change notice.

    Resolves the current address from the primary and retries a backend rejection through the
    project task policy. A duplicate notice after worker-loss redelivery is harmless because it
    carries no credential, action, or account data.

    Arguments:
        account_id: Immutable account identifier.

    Returns:
        True when delivered or when no live account remains.

    Raises:
        AccountEmailDeliveryError: If the configured backend rejects the message.
    """
    recipient = _notification_recipient(account_id)
    if recipient is None:
        return True
    if send_application_email(
        recipient,
        "Your LocalForge password changed",
        "Your password was changed through account recovery.",
    ):
        return True

    raise AccountEmailDeliveryError


def deliver_username_changed_email(account_id: object) -> bool:
    """Deliver one credential-free username-change notice.

    Resolves the current address from the primary and retries a backend rejection through the
    project task policy. A duplicate notice after worker-loss redelivery is harmless because it
    carries no credential, action, or account data.

    Arguments:
        account_id: Immutable account identifier.

    Returns:
        True when delivered or when no live account remains.

    Raises:
        AccountEmailDeliveryError: If the configured backend rejects the message.
    """
    recipient = _notification_recipient(account_id)
    if recipient is None:
        return True
    if send_application_email(
        recipient,
        "Your LocalForge username changed",
        "Your username was changed.",
    ):
        try:
            publish_notification(
                uuid.UUID(cast("str", account_id)),
                "account.username_changed",
                {},
            )
        except Exception as error:
            logger.log(
                logging.ERROR,
                "Username notification publication failed",
                extra={"notification_error_type": type(error).__name__},
            )
        return True

    raise AccountEmailDeliveryError


send_password_changed_email = cast(
    "NotificationEmailTask",
    app.task(name="accounts.send_password_changed_email")(deliver_password_changed_email),
)

send_username_changed_email = cast(
    "NotificationEmailTask",
    app.task(name="accounts.send_username_changed_email")(deliver_username_changed_email),
)
