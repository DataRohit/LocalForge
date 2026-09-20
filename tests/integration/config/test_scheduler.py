"""Integration tests for database-backed periodic maintenance.

Exercises the persisted django-celery-beat schedule and the maintenance tasks it dispatches through
the authoritative primary database.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import timedelta
from importlib import import_module
from typing import TYPE_CHECKING, cast

import psycopg
import pytest
from django.apps import apps as django_apps
from django.conf import settings
from django.contrib import admin
from django.db import connections
from django.test import override_settings
from django.utils import timezone
from django_celery_beat.models import CrontabSchedule, IntervalSchedule, PeriodicTask
from django_celery_beat.schedulers import DatabaseScheduler
from freezegun import freeze_time
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken

from accounts.models import ActivationToken, PasswordResetToken, User, UsernameResetToken
from config.celery import app
from config.tasks import (
    ACCOUNT_CLEANUP_LOCK_SCOPE,
    cleanup_expired_account_tokens,
    flush_expired_jwt_tokens,
)

if TYPE_CHECKING:
    from collections.abc import Callable

JWT_SCHEDULE_NAME = "localforge.flush-expired-jwt-tokens"
TOMBSTONE_SCHEDULE_NAME = "localforge.cleanup-expired-account-tokens"
JWT_TASK_NAME = "config.flush_expired_jwt_tokens"
TOMBSTONE_TASK_NAME = "config.cleanup_expired_account_tokens"


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_database_schedules_are_seeded_editable_and_persistent() -> None:
    """Persist both maintenance schedules through the admin-backed database models.

    Loads the migration-seeded rows, verifies their queue and expiry contracts, edits one through
    the model seam, and reloads it to prove a scheduler restart sees persisted state.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If either schedule is missing, hardcoded in Celery, or not persistent.
    """
    migration = import_module("accounts.migrations.0008_schedule_maintenance")
    with connections["default"].schema_editor() as schema_editor:
        migration.seed_schedules(django_apps, schema_editor)

    jwt = PeriodicTask.objects.using("default").get(name=JWT_SCHEDULE_NAME)
    tombstones = PeriodicTask.objects.using("default").get(name=TOMBSTONE_SCHEDULE_NAME)

    assert jwt.task == JWT_TASK_NAME
    assert jwt.crontab is not None
    assert jwt.interval is None
    assert jwt.queue == settings.CELERY_DEFAULT_QUEUE
    assert jwt.enabled is True
    assert jwt.expire_seconds is not None
    assert tombstones.task == TOMBSTONE_TASK_NAME
    assert tombstones.interval is not None
    assert tombstones.interval.period == IntervalSchedule.SECONDS
    assert tombstones.interval.every == settings.CELERY_TOMBSTONE_CLEANUP_INTERVAL_SECONDS
    assert tombstones.queue == settings.CELERY_SLOW_QUEUE
    assert tombstones.expire_seconds == settings.CELERY_TOMBSTONE_CLEANUP_INTERVAL_SECONDS
    assert tombstones.enabled is True

    tombstones.description = "edited without redeploy"
    tombstones.save(using="default", update_fields=("description",))
    tombstones.refresh_from_db(using="default")

    assert tombstones.description == "edited without redeploy"

    with connections["default"].schema_editor() as schema_editor:
        migration.remove_schedules(django_apps, schema_editor)

    assert (
        not PeriodicTask.objects.using("default")
        .filter(name__in=(JWT_SCHEDULE_NAME, TOMBSTONE_SCHEDULE_NAME))
        .exists()
    )


@pytest.mark.integration
@pytest.mark.services("postgres")
def test_scheduler_models_are_available_through_the_django_admin() -> None:
    """Expose persisted schedules through the existing administration interface.

    Requires the dependency's periodic, interval, and crontab models to be registered, so changing
    a schedule needs no image rebuild or Compose recreation.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If any schedule model is unavailable to an administrator.
    """
    assert admin.site.is_registered(PeriodicTask)
    assert admin.site.is_registered(IntervalSchedule)
    assert admin.site.is_registered(CrontabSchedule)


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_database_scheduler_reads_schedule_state_from_the_primary() -> None:
    """Load the real database scheduler without consulting the read replica.

    Seeds the persisted schedules, rejects every replica statement, and requires the scheduler's
    own schedule-loading path to read authoritative primary state.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If DatabaseScheduler performs any read through the replica.
    """
    migration = import_module("accounts.migrations.0008_schedule_maintenance")
    with connections["default"].schema_editor() as schema_editor:
        migration.seed_schedules(django_apps, schema_editor)
    primary_statements: list[str] = []

    def capture_primary(execute: object, sql: object, *arguments: object) -> object:
        """Record and execute one scheduler statement on the primary.

        Preserves the statement while collecting evidence that the real scheduler selected the
        authoritative connection.

        Arguments:
            execute: Django database execution callback.
            sql: Statement sent to the primary.
            *arguments: Remaining execution-wrapper arguments.

        Returns:
            Database driver's statement result.

        Raises:
            TypeError: If Django supplies a non-string statement.
        """
        if not isinstance(sql, str):
            message = "scheduler supplied non-string SQL"
            raise TypeError(message)
        primary_statements.append(sql)
        return cast("Callable[..., object]", execute)(sql, *arguments)

    def reject_replica(_execute: object, _sql: object, *_arguments: object) -> None:
        """Reject every scheduler statement sent to the replica.

        Fails at the database boundary so the testing mirror cannot hide a stale-read routing
        decision.

        Arguments:
            _execute: Django database execution callback.
            _sql: Statement sent to the replica.
            *_arguments: Remaining execution-wrapper arguments.

        Returns:
            Never returns.

        Raises:
            AssertionError: Always, because schedule state is authoritative.
        """
        pytest.fail("DatabaseScheduler queried the replica")

    scheduler = DatabaseScheduler(app=app, lazy=True)
    with (
        connections["default"].execute_wrapper(capture_primary),
        connections["replica"].execute_wrapper(reject_replica),
    ):
        schedule = scheduler.all_as_schedule()

    assert primary_statements
    assert TOMBSTONE_SCHEDULE_NAME in schedule


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_jwt_cleanup_task_runs_the_upstream_command_on_primary(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Remove expired JWT state while preserving unexpired rows.

    Executes the registered task eagerly against the real primary database and proves the upstream
    command's expired-token cascade is the behavior the scheduler will dispatch.

    Arguments:
        caplog: Fixture capturing the visible scheduler success record.

    Returns:
        None.

    Raises:
        AssertionError: If expired state survives or unexpired state is removed.
    """
    account = User.objects.db_manager("default").create_user(
        f"scheduled-jwt-{uuid.uuid4().hex}",
        f"scheduled-jwt-{uuid.uuid4().hex}@localforge.invalid",
        secrets.token_urlsafe(24),
        is_active=True,
    )
    now = timezone.now()
    expired = OutstandingToken.objects.using("default").create(
        user=account,
        jti=uuid.uuid4().hex,
        token=secrets.token_urlsafe(24),
        created_at=now - timedelta(days=2),
        expires_at=now - timedelta(days=1),
    )
    unexpired = OutstandingToken.objects.using("default").create(
        user=account,
        jti=uuid.uuid4().hex,
        token=secrets.token_urlsafe(24),
        created_at=now,
        expires_at=now + timedelta(days=1),
    )
    BlacklistedToken.objects.using("default").create(token=expired)
    live_blacklist = BlacklistedToken.objects.using("default").create(token=unexpired)

    with caplog.at_level("INFO", logger="config.tasks"):
        result = flush_expired_jwt_tokens()

    assert result == {"flushed": 1}
    assert not OutstandingToken.objects.using("default").filter(pk=expired.pk).exists()
    assert not BlacklistedToken.objects.using("default").filter(token_id=expired.pk).exists()
    assert OutstandingToken.objects.using("default").filter(pk=unexpired.pk).exists()
    assert BlacklistedToken.objects.using("default").filter(pk=live_blacklist.pk).exists()
    assert caplog.records[-1].__dict__["periodic_task"] == "flush_expired_jwt_tokens"
    assert caplog.records[-1].__dict__["flushed"] == 1


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@override_settings(CELERY_TOMBSTONE_CLEANUP_BATCH_SIZE=2)
def test_tombstone_cleanup_uses_oldest_first_bounded_primary_batches() -> None:
    """Delete only expired tombstones in deterministic bounded batches.

    Creates three expired and one live record for every account-token model, then requires two
    oldest rows per model to disappear on the first run and the final expired row on the second.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If cleanup exceeds its bound, changes order, or removes live state.
    """
    now = timezone.now()
    models = (ActivationToken, PasswordResetToken, UsernameResetToken)
    records: dict[type[ActivationToken | PasswordResetToken | UsernameResetToken], list[int]] = {}

    for model in models:
        identifiers: list[int] = []
        for age_days in (5, 4, 3):
            record = model.objects.using("default").create(
                account=None,
                subject_id=uuid.uuid4(),
                digest=secrets.token_hex(32),
            )
            model.objects.using("default").filter(pk=record.pk).update(
                issued_at=now - timedelta(days=age_days)
            )
            identifiers.append(record.pk)
        live = model.objects.using("default").create(
            account=None,
            subject_id=uuid.uuid4(),
            digest=secrets.token_hex(32),
        )
        identifiers.append(live.pk)
        records[model] = identifiers

    first = cleanup_expired_account_tokens()

    assert first == {
        "activation": 2,
        "password_reset": 2,
        "skipped": 0,
        "username_reset": 2,
    }
    for model, identifiers in records.items():
        assert not model.objects.using("default").filter(pk__in=identifiers[:2]).exists()
        assert model.objects.using("default").filter(pk=identifiers[2]).exists()
        assert model.objects.using("default").filter(pk=identifiers[3]).exists()

    second = cleanup_expired_account_tokens()

    assert second == {
        "activation": 1,
        "password_reset": 1,
        "skipped": 0,
        "username_reset": 1,
    }
    for model, identifiers in records.items():
        assert not model.objects.using("default").filter(pk=identifiers[2]).exists()
        assert model.objects.using("default").filter(pk=identifiers[3]).exists()

    third = cleanup_expired_account_tokens()

    assert third == {
        "activation": 0,
        "password_reset": 0,
        "skipped": 0,
        "username_reset": 0,
    }


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@override_settings(
    ACCOUNT_ACTIVATION_TOKEN_LIFETIME_SECONDS=60,
    PASSWORD_RESET_TIMEOUT=60,
    USERNAME_RESET_TIMEOUT=60,
)
@freeze_time("2026-09-20 12:00:00.500000+00:00")
def test_tombstone_cleanup_preserves_the_inclusive_token_boundary() -> None:
    """Retain whole-second bearers until strictly after their configured lifetime.

    Places one row at the exact inclusive timeout and one a second beyond it for every token model,
    then requires cleanup to preserve the boundary row and remove only the older row.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If cleanup removes a still-valid boundary token or retains an expired one.
    """
    now = timezone.now()
    models = (ActivationToken, PasswordResetToken, UsernameResetToken)
    rows: list[
        tuple[
            type[ActivationToken | PasswordResetToken | UsernameResetToken],
            int,
            int,
        ]
    ] = []

    for model in models:
        boundary = model.objects.using("default").create(
            account=None,
            subject_id=uuid.uuid4(),
            digest=secrets.token_hex(32),
        )
        expired = model.objects.using("default").create(
            account=None,
            subject_id=uuid.uuid4(),
            digest=secrets.token_hex(32),
        )
        model.objects.using("default").filter(pk=boundary.pk).update(
            issued_at=now - timedelta(seconds=60)
        )
        model.objects.using("default").filter(pk=expired.pk).update(
            issued_at=now - timedelta(seconds=61)
        )
        rows.append((model, boundary.pk, expired.pk))

    cleanup_expired_account_tokens()

    for token_model, boundary_id, expired_id in rows:
        assert token_model.objects.using("default").filter(pk=boundary_id).exists()
        assert not token_model.objects.using("default").filter(pk=expired_id).exists()


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_tombstone_cleanup_skips_while_another_run_holds_the_database_lock(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Avoid stacking cleanup invocations when an earlier run still owns the work.

    Holds the database-scoped advisory lock through an independent connection and requires the task
    to return a visible skipped outcome without deleting an expired row.

    Arguments:
        caplog: Fixture capturing the visible overlap-skipped record.

    Returns:
        None.

    Raises:
        AssertionError: If overlapping cleanup runs or removes state.
    """
    record = ActivationToken.objects.using("default").create(
        account=None,
        subject_id=uuid.uuid4(),
        digest=secrets.token_hex(32),
    )
    ActivationToken.objects.using("default").filter(pk=record.pk).update(
        issued_at=timezone.now() - timedelta(days=2)
    )
    database = settings.DATABASES["default"]

    with psycopg.connect(
        host=database["HOST"],
        port=database["PORT"],
        dbname=database["NAME"],
        user=database["USER"],
        password=database["PASSWORD"],
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_lock(hashtextextended(current_database() || %s, 0))",
                [ACCOUNT_CLEANUP_LOCK_SCOPE],
            )
        with caplog.at_level("WARNING", logger="config.tasks"):
            result = cleanup_expired_account_tokens()

    assert result == {
        "activation": 0,
        "password_reset": 0,
        "skipped": 1,
        "username_reset": 0,
    }
    assert ActivationToken.objects.using("default").filter(pk=record.pk).exists()
    assert connections["replica"].in_atomic_block is False
    assert caplog.records[-1].__dict__["periodic_task"] == "cleanup_expired_account_tokens"
    assert caplog.records[-1].__dict__["skipped"] == 1
