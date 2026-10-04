from __future__ import annotations

from typing import TYPE_CHECKING

from django.db import migrations

if TYPE_CHECKING:
    from django.apps.registry import Apps
    from django.db.backends.base.schema import BaseDatabaseSchemaEditor

LEGACY_DEFAULT_QUEUE = "localforge.default"
LEGACY_SLOW_QUEUE = "localforge.slow"
LEGACY_DEAD_LETTER_QUEUE = "localforge.dead-letter"
VERSIONED_DEFAULT_QUEUE = "localforge.v2.default"
VERSIONED_SLOW_QUEUE = "localforge.v2.slow"
VERSIONED_DEAD_LETTER_QUEUE = "localforge.v2.dead-letter"
QUEUE_RENAMES = {
    LEGACY_DEFAULT_QUEUE: VERSIONED_DEFAULT_QUEUE,
    LEGACY_SLOW_QUEUE: VERSIONED_SLOW_QUEUE,
    LEGACY_DEAD_LETTER_QUEUE: VERSIONED_DEAD_LETTER_QUEUE,
}


def version_queues(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
    database = schema_editor.connection.alias
    for legacy, versioned in QUEUE_RENAMES.items():
        PeriodicTask.objects.using(database).filter(queue=legacy).update(queue=versioned)


def restore_queues(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
    database = schema_editor.connection.alias
    for legacy, versioned in QUEUE_RENAMES.items():
        PeriodicTask.objects.using(database).filter(queue=versioned).update(queue=legacy)


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0008_schedule_maintenance"),
    ]

    operations = [
        migrations.RunPython(version_queues, restore_queues),
    ]
