"""Authoritative token-login admission state.

Serializes address and account rolling windows through PostgreSQL advisory locks and persists every
admitted request beside account state, so cache eviction, cache flushes, and application restarts
cannot reset the security boundary.
"""

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import cast

from django.db import connections, transaction

from accounts.models import LoginThrottleEvent

GLOBAL_CLEANUP_BATCH_SIZE = 64
GLOBAL_RETENTION_SECONDS = 86400
MAX_RULES_PER_ADMISSION = 3
GLOBAL_CLEANUP_LOCK = "login-throttle:global-cleanup"


@dataclass(frozen=True, slots=True)
class RollingWindowRule:
    """Describe one authoritative rolling-window dimension.

    Carries the opaque database bucket, exact admission limit, and duration consumed atomically
    with every other rule in one login decision.

    Attributes:
        key: Opaque dimension identity used for shared storage.
        limit: Maximum admitted requests inside the window.
        window_seconds: Rolling window duration in seconds.

    Members:
        None.
    """

    key: str
    limit: int
    window_seconds: int


@dataclass(frozen=True, slots=True)
class ThrottleDecision:
    """Represent one authoritative primary-database decision.

    Records whether the request was admitted and the complete retry delay calculated from the
    oldest still-active event when any dimension is full.

    Attributes:
        admitted: Whether every rolling-window dimension admitted the request.
        retry_after_seconds: Delay before the fullest dimension can admit another request.

    Members:
        None.
    """

    admitted: bool
    retry_after_seconds: int


class PostgresLoginThrottleStore:
    """Execute exact rolling-window admission on the primary database.

    Inherits nothing and acquires deterministic transaction-scoped advisory locks before selecting
    active events and recording every dimension, making one decision serializable across workers.

    Attributes:
        database_alias: Django database alias holding account and admission state.

    Members:
        admit: Atomically decide and record all supplied rolling-window dimensions.
    """

    def __init__(self, database_alias: str) -> None:
        """Bind the store to one authoritative database alias.

        Keeps the alias explicit so application code and tests cannot silently follow a replica
        router for security writes.

        Arguments:
            database_alias: Django database alias used for all operations.

        Returns:
            None.
        """
        self.database_alias = database_alias

    def admit(
        self,
        rules: tuple[RollingWindowRule, ...],
        *,
        member: str,
    ) -> ThrottleDecision:
        """Make one exact shared rolling-window decision.

        Prunes one bounded global retention batch, locks each opaque bucket in lexical order, and
        inserts every dimension only when its active primary-timed window has capacity.

        Arguments:
            rules: Address and optional account rules to enforce together.
            member: Unique correlated request identifier stored in each admitted bucket.

        Returns:
            Authoritative admission result and retry delay.

        Raises:
            ValueError: If no rule is supplied or a rule is invalid.
            DatabaseError: If authoritative state cannot be read or written.
        """
        if not rules:
            message = "at least one rolling-window rule is required"
            raise ValueError(message)
        if len(rules) > MAX_RULES_PER_ADMISSION:
            message = "at most three rolling-window rules are supported"
            raise ValueError(message)
        if len({rule.key for rule in rules}) != len(rules):
            message = "rolling-window rule keys must be unique"
            raise ValueError(message)
        if any(
            rule.limit <= 0
            or rule.window_seconds <= 0
            or rule.window_seconds > GLOBAL_RETENTION_SECONDS
            for rule in rules
        ):
            message = "rolling-window limits and durations must be positive and retained"
            raise ValueError(message)

        self._prune_globally_expired_events()
        ordered_keys = sorted({rule.key for rule in rules})
        connection = connections[self.database_alias]
        with transaction.atomic(using=self.database_alias), connection.cursor() as cursor:
            for key in ordered_keys:
                cursor.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    [key],
                )

            cursor.execute("SELECT clock_timestamp()")
            now = cast("datetime", cast("tuple[object]", cursor.fetchone())[0])
            retry_after_seconds = 0

            for rule in rules:
                cutoff = now - timedelta(seconds=rule.window_seconds)
                events = LoginThrottleEvent.objects.using(self.database_alias).filter(
                    bucket=rule.key,
                    occurred_at__gt=cutoff,
                )
                if events.count() >= rule.limit:
                    oldest = events.order_by("occurred_at").values_list(
                        "occurred_at",
                        flat=True,
                    )[0]
                    remaining = (
                        oldest + timedelta(seconds=rule.window_seconds) - now
                    ).total_seconds()
                    retry_after_seconds = max(
                        retry_after_seconds,
                        1,
                        math.ceil(remaining),
                    )

            if retry_after_seconds:
                return ThrottleDecision(
                    admitted=False,
                    retry_after_seconds=retry_after_seconds,
                )

            LoginThrottleEvent.objects.using(self.database_alias).bulk_create(
                [
                    LoginThrottleEvent(
                        bucket=rule.key,
                        request_id=member,
                        occurred_at=now,
                    )
                    for rule in rules
                ]
            )

        return ThrottleDecision(admitted=True, retry_after_seconds=0)

    def _prune_globally_expired_events(self) -> None:
        """Delete one deterministic bounded batch beyond the longest supported window.

        Serializes cleanup independently of admission identities and removes more expired rows per
        call than one valid admission can insert, while leaving each bucket's exact window decision
        in its own transaction.

        Arguments:
            None.

        Returns:
            None.

        Raises:
            DatabaseError: If authoritative cleanup state cannot be read or written.
        """
        connection = connections[self.database_alias]
        with transaction.atomic(using=self.database_alias), connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                [GLOBAL_CLEANUP_LOCK],
            )
            cursor.execute("SELECT clock_timestamp()")
            now = cast("datetime", cast("tuple[object]", cursor.fetchone())[0])
            cutoff = now - timedelta(seconds=GLOBAL_RETENTION_SECONDS)
            expired_ids = list(
                LoginThrottleEvent.objects.using(self.database_alias)
                .filter(occurred_at__lte=cutoff)
                .order_by("occurred_at", "id")
                .values_list("id", flat=True)[:GLOBAL_CLEANUP_BATCH_SIZE]
            )
            if expired_ids:
                LoginThrottleEvent.objects.using(self.database_alias).filter(
                    id__in=expired_ids
                ).delete()
