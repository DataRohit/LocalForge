"""Authoritative scheduler and application database routing.

Pins django-celery-beat to primary state, then sends ordinary application reads to the standby and
all writes to the primary. A read inside an atomic block is the application exception.
"""

from typing import Any

from django.db import connections

PRIMARY = "default"
REPLICA = "replica"
CELERY_BEAT_APP_LABEL = "django_celery_beat"


class CeleryBeatRouter:
    """Pin database scheduler state to the authoritative primary.

    Inherits nothing; Django consults this narrow router before the catch-all primary-replica
    router so schedule edits, due state, and restart counters never depend on replica freshness.

    Attributes:
        None.

    Members:
        db_for_read: Route scheduler reads to the primary.
        db_for_write: Route scheduler writes to the primary.
        allow_migrate: Permit scheduler migrations only on the primary.
    """

    @staticmethod
    def _is_scheduler_model(model: type) -> bool:
        """Identify one model owned by django-celery-beat.

        Uses the public defining module rather than Django's private model metadata, keeping the
        routing decision narrow without binding to an internal attribute.

        Arguments:
            model: Model class under consideration.

        Returns:
            Whether the model belongs to django-celery-beat.

        Raises:
            None.
        """
        return model.__module__.partition(".")[0] == CELERY_BEAT_APP_LABEL

    def db_for_read(self, model: type, **hints: Any) -> str | None:  # noqa: ANN401, ARG002
        """Route scheduler reads to the primary.

        Returns no decision for every other application so the ordinary primary-replica router
        remains responsible for application read distribution.

        Arguments:
            model: Model being read.
            **hints: Routing hints Django supplies.

        Returns:
            The primary alias for django-celery-beat models, otherwise None.

        Raises:
            None.
        """
        if self._is_scheduler_model(model):
            return PRIMARY

        return None

    def db_for_write(self, model: type, **hints: Any) -> str | None:  # noqa: ANN401, ARG002
        """Route scheduler writes to the primary.

        Keeps due counters and administration edits on the same authoritative connection the
        scheduler reads, while deferring unrelated models to the catch-all router.

        Arguments:
            model: Model being written.
            **hints: Routing hints Django supplies.

        Returns:
            The primary alias for django-celery-beat models, otherwise None.

        Raises:
            None.
        """
        if self._is_scheduler_model(model):
            return PRIMARY

        return None

    def allow_migrate(self, db: str, app_label: str, **hints: Any) -> bool | None:  # noqa: ANN401, ARG002
        """Permit scheduler migrations only on the primary.

        Defers every unrelated application to the existing catch-all migration rule and explicitly
        refuses the read-only replica for django-celery-beat.

        Arguments:
            db: Alias the migration would run against.
            app_label: Application the migration belongs to.
            **hints: Routing hints Django supplies.

        Returns:
            Whether scheduler migration is permitted, or None for unrelated applications.

        Raises:
            None.
        """
        if app_label == CELERY_BEAT_APP_LABEL:
            return db == PRIMARY

        return None


class PrimaryReplicaRouter:
    """Route ordinary application reads to the standby and writes to the primary.

    Inherits nothing; Django discovers routers by the methods they provide. It answers after the
    scheduler-specific router for every remaining model, so it is the **last** entry in
    ``DATABASE_ROUTERS``.

    Attributes:
        None.

    Members:
        db_for_read: Choose the alias a read is served from.
        db_for_write: Choose the alias a write is sent to.
        allow_relation: Report whether two objects may be related.
        allow_migrate: Report whether a migration may run on an alias.
    """

    def db_for_read(self, model: type, **hints: Any) -> str:  # noqa: ANN401, ARG002
        """Choose the alias a read is served from.

        Sends reads to the standby, except while an atomic block is open on the primary: Django
        does not reroute for transactions by itself, so without this a block would write to one
        connection and read from another that cannot see the uncommitted rows.

        Arguments:
            model: Model being read.
            **hints: Routing hints Django supplies.

        Returns:
            The primary alias inside an atomic block, the replica alias otherwise.

        Raises:
            None.
        """
        if connections[PRIMARY].in_atomic_block:
            return PRIMARY

        return REPLICA

    def db_for_write(self, model: type, **hints: Any) -> str:  # noqa: ANN401, ARG002
        """Choose the alias a write is sent to.

        Sends every write to the primary, since the standby is physically read-only and would
        reject the statement rather than forward it.

        Arguments:
            model: Model being written.
            **hints: Routing hints Django supplies.

        Returns:
            The primary alias.

        Raises:
            None.
        """
        return PRIMARY

    def allow_relation(self, first: object, second: object, **hints: Any) -> bool:  # noqa: ANN401, ARG002
        """Report whether two objects may be related.

        Permits every relation, because both aliases address one physical cluster: the standby is a
        byte-for-byte copy of the primary rather than a separate database with its own contents.

        Arguments:
            first: One object in the relation.
            second: The other object in the relation.
            **hints: Routing hints Django supplies.

        Returns:
            True, always.

        Raises:
            None.
        """
        return True

    def allow_migrate(self, db: str, app_label: str, **hints: Any) -> bool:  # noqa: ANN401, ARG002
        """Report whether a migration may run on an alias.

        Permits migration only on the primary. **This is the project's own rule, not a framework
        default**: Django's own example returns true unconditionally, because the replicas it
        imagines are separately migrated databases rather than a physical read-only standby.

        Arguments:
            db: Alias the migration would run against.
            app_label: Application the migration belongs to.
            **hints: Routing hints Django supplies.

        Returns:
            True only for the primary alias.

        Raises:
            None.
        """
        return db == PRIMARY
