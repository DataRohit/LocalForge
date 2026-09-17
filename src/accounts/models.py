"""User model for the platform.

Holds the account every authenticated request resolves to, with the identifiers the account
endpoints need and an activation flag that gates sign-in until the address is confirmed.
"""

import uuid
from typing import TYPE_CHECKING, Any, override

from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.core.validators import validate_email
from django.db import models
from django.db.models.functions import Lower

from accounts.managers import UserManager
from accounts.normalisation import normalise_email

if TYPE_CHECKING:
    from collections.abc import Collection

LOGIN_THROTTLE_TABLE = "accounts_login_throttle_event"


class User(AbstractBaseUser, PermissionsMixin):
    """One account on the platform.

    Inherits from ``AbstractBaseUser`` for password handling and ``PermissionsMixin`` for the
    permission framework, and identifies accounts by a time-ordered random key: it reveals when an
    account was created, which this platform accepts, but never how many accounts exist.

    Attributes:
        id: Non-sequential, time-ordered primary key.
        username: Unique username, unique without regard to letter case.
        email: Unique address, stored lowercased so a lookup by address is an exact match.
        is_active: Whether the account may sign in; false until it is activated.
        is_staff: Whether the account may reach the administration interface.
        created_at: When the account was created.
        updated_at: When the account was last modified.
        objects: Manager every account is created through.

    Members:
        clean_fields: Reduce the address to its canonical form before it is validated.
        save: Normalise and validate the address, then write the account.
        __str__: Render the account as its username.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    username = models.CharField(max_length=150, unique=True)
    email = models.EmailField(max_length=254, unique=True)
    is_active = models.BooleanField(default=False)
    is_staff = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = UserManager()

    USERNAME_FIELD = "username"
    EMAIL_FIELD = "email"
    REQUIRED_FIELDS = ["email"]  # noqa: RUF012

    class Meta:
        """Database-level rules for the account table.

        Adds the case-insensitive uniqueness the identifiers need, which a plain unique column does
        not give, and orders accounts by creation and key so pagination is stable.

        Attributes:
            ordering: Default ordering for queries that specify none.
            constraints: Case-insensitive uniqueness for both identifiers.

        Members:
            None.
        """

        ordering = ("created_at", "id")
        constraints = (
            models.UniqueConstraint(
                Lower("username"),
                name="accounts_user_username_ci_unique",
                violation_error_message="That username is not available.",
            ),
            models.UniqueConstraint(
                Lower("email"),
                name="accounts_user_email_ci_unique",
                violation_error_message="That account could not be created.",
            ),
        )

    @override
    def clean_fields(self, exclude: Collection[str] | None = None) -> None:
        """Reduce the address to its canonical form before it is validated.

        Runs ahead of field validation wherever full validation happens, so the value a uniqueness
        check sees is the value that will be stored, and a surrounding space is trimmed rather than
        reported as an invalid address.

        Arguments:
            exclude: Field names to leave unvalidated.

        Returns:
            None.

        Raises:
            ValidationError: If any validated field rejects its value.
        """
        self.email = normalise_email(self.email)

        super().clean_fields(exclude=exclude)

    @override
    def save(self, *args: Any, **keywords: Any) -> None:
        """Normalise and validate the address, then write the account.

        Canonicalises the whole address and rejects an unparsable one before it reaches the
        database, because the case-insensitive constraint would otherwise be the first to notice.

        Arguments:
            *args: Positional arguments for the base implementation.
            **keywords: Keyword arguments for the base implementation.

        Returns:
            None.

        Raises:
            ValidationError: If the address is not a valid email address.
        """
        self.email = normalise_email(self.email)
        validate_email(self.email)

        super().save(*args, **keywords)

    @override
    def __str__(self) -> str:
        """Render the account as its username.

        Returns the identifier a human recognises, which is what the administration interface and
        log records show.

        Arguments:
            None.

        Returns:
            The account's username.

        Raises:
            None.
        """
        return str(self.username)


class LoginThrottleEvent(models.Model):
    """One admitted login attempt in an authoritative rolling window.

    Inherits from ``Model`` and stores only opaque bucket and request identifiers beside the
    primary-database timestamp used to make admission decisions across application processes.

    Attributes:
        id: Database-generated event identifier.
        bucket: Opaque address or account dimension.
        request_id: Correlated request identifier unique inside one bucket.
        occurred_at: Primary-database time at which the request was admitted.

    Members:
        __str__: Render the opaque bucket and correlated request identifier.
    """

    bucket = models.CharField(max_length=160)
    request_id = models.CharField(max_length=80)
    occurred_at = models.DateTimeField()

    class Meta:
        """Database rules for authoritative login admission events.

        Indexes each bucket chronologically for exact admission and all events chronologically for
        bounded retention, while preventing duplicate request identifiers inside one dimension.

        Attributes:
            db_table: Stable table name used by outage diagnostics and migrations.
            indexes: Chronological lookup paths for one bucket and global retention.
            constraints: Per-bucket request identifier uniqueness.

        Members:
            None.
        """

        db_table = LOGIN_THROTTLE_TABLE
        indexes = (
            models.Index(
                fields=("bucket", "occurred_at"),
                name="accounts_login_bucket_time",
            ),
            models.Index(
                fields=("occurred_at", "id"),
                name="accounts_login_occurred_id",
            ),
        )
        constraints = (
            models.UniqueConstraint(
                fields=("bucket", "request_id"),
                name="accounts_login_bucket_request_unique",
            ),
        )

    @override
    def __str__(self) -> str:
        """Render the opaque bucket and correlated request identifier.

        Returns only identifiers already persisted for operational diagnosis and never resolves
        either value back to a submitted address or account name.

        Arguments:
            None.

        Returns:
            Stable opaque event description.

        Raises:
            None.
        """
        return f"{self.bucket}:{self.request_id}"


class ActivationToken(models.Model):
    """One issued account-activation token.

    Inherits from ``Model`` and stores a digest, immutable subject, nullable live account, use
    state, and durable delivery timestamps so deletion preserves classification and redelivery is
    at most once.

    Attributes:
        id: Database-generated token record identifier.
        account: Live account the token can activate, or null after account deletion.
        subject_id: Immutable account identifier retained independently of the live account.
        digest: SHA-256 digest of the signed token.
        issued_at: Time the token record was created.
        used_at: Time activation consumed the token, or null while unused.
        delivery_claimed_at: Time one worker durably claimed the sole SMTP attempt.
        delivered_at: Time SMTP accepted the claimed message, or null if it failed.

    Members:
        __str__: Render the owning account and record keys without token material.
    """

    account = models.ForeignKey(
        User,
        null=True,
        on_delete=models.SET_NULL,
        related_name="activation_tokens",
    )
    subject_id = models.UUIDField()
    digest = models.CharField(max_length=64, unique=True)
    issued_at = models.DateTimeField(auto_now_add=True)
    used_at = models.DateTimeField(null=True)
    delivery_claimed_at = models.DateTimeField(null=True)
    delivered_at = models.DateTimeField(null=True)

    class Meta:
        """Database rules for activation token records.

        Orders records newest first and indexes immutable subject state plus issue time for
        activation classification and the scheduler-owned bounded-retention cleanup.

        Attributes:
            ordering: Newest token records first.
            indexes: Subject use-state and chronological retention lookup paths.

        Members:
            None.
        """

        ordering = ("-issued_at", "-id")
        indexes = (
            models.Index(
                fields=("subject_id", "used_at"),
                name="accounts_activation_subject_used",
            ),
            models.Index(
                fields=("issued_at", "id"),
                name="accounts_activation_issued_id",
            ),
        )

    @override
    def __str__(self) -> str:
        """Render the record without exposing its token digest.

        Returns the owning account and record keys, which identify the row for administration
        without copying activation credential material into a display or log.

        Arguments:
            None.

        Returns:
            Safe activation-token record description.

        Raises:
            None.
        """
        return f"{self.subject_id}:{self.pk}"
