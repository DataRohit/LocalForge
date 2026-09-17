"""Account email background tasks.

Registers account-message delivery with the project Celery application so web requests enqueue
mail work while testing can retain the repository's eager task seam.
"""

import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast
from urllib.parse import urlencode

from django.core import signing
from django.db import transaction
from django.utils import timezone

from accounts.activation_tokens import activation_token_digest, load_activation_payload
from accounts.models import ActivationToken, User
from config.celery import app
from config.email import send_application_email

if TYPE_CHECKING:
    from typing import Protocol

    from celery import Celery
    from celery.result import AsyncResult

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
        except signing.BadSignature, TypeError, ValueError:
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
    except signing.BadSignature, TypeError, ValueError:
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
