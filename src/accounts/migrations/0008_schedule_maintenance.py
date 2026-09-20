from __future__ import annotations

from typing import TYPE_CHECKING

from django.conf import settings
from django.db import migrations

if TYPE_CHECKING:
    from django.apps.registry import Apps
    from django.db.backends.base.schema import BaseDatabaseSchemaEditor

JWT_SCHEDULE_NAME = "localforge.flush-expired-jwt-tokens"
TOMBSTONE_SCHEDULE_NAME = "localforge.cleanup-expired-account-tokens"
JWT_TASK_NAME = "config.flush_expired_jwt_tokens"
TOMBSTONE_TASK_NAME = "config.cleanup_expired_account_tokens"


def seed_schedules(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    CrontabSchedule = apps.get_model("django_celery_beat", "CrontabSchedule")
    IntervalSchedule = apps.get_model("django_celery_beat", "IntervalSchedule")
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
    database = schema_editor.connection.alias
    crontab, _ = CrontabSchedule.objects.using(database).get_or_create(
        minute=str(settings.CELERY_JWT_CLEANUP_MINUTE),
        hour=str(settings.CELERY_JWT_CLEANUP_HOUR),
        day_of_week="*",
        day_of_month="*",
        month_of_year="*",
        timezone=settings.TIME_ZONE,
    )
    interval, _ = IntervalSchedule.objects.using(database).get_or_create(
        every=settings.CELERY_TOMBSTONE_CLEANUP_INTERVAL_SECONDS,
        period="seconds",
    )
    PeriodicTask.objects.using(database).update_or_create(
        name=JWT_SCHEDULE_NAME,
        defaults={
            "task": JWT_TASK_NAME,
            "crontab": crontab,
            "interval": None,
            "args": "[]",
            "kwargs": "{}",
            "queue": settings.CELERY_DEFAULT_QUEUE,
            "expire_seconds": settings.CELERY_JWT_CLEANUP_EXPIRY_SECONDS,
            "enabled": True,
            "description": "Daily primary-database cleanup of expired JWT state.",
        },
    )
    PeriodicTask.objects.using(database).update_or_create(
        name=TOMBSTONE_SCHEDULE_NAME,
        defaults={
            "task": TOMBSTONE_TASK_NAME,
            "interval": interval,
            "crontab": None,
            "args": "[]",
            "kwargs": "{}",
            "queue": settings.CELERY_SLOW_QUEUE,
            "expire_seconds": settings.CELERY_TOMBSTONE_CLEANUP_INTERVAL_SECONDS,
            "enabled": True,
            "description": "Bounded primary cleanup of expired account-token tombstones.",
        },
    )


def remove_schedules(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
    database = schema_editor.connection.alias
    PeriodicTask.objects.using(database).filter(
        name__in=(JWT_SCHEDULE_NAME, TOMBSTONE_SCHEDULE_NAME)
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0007_shorten_activation_index"),
        ("django_celery_beat", "0019_alter_periodictasks_options"),
    ]

    operations = [
        migrations.RunPython(seed_schedules, remove_schedules),
    ]
