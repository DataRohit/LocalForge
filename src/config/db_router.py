"""Database routing between the primary and its read replica.

Sends reads to the standby and everything that writes to the primary, so a read-heavy request does
not consume the primary's capacity and a write never reaches a server that cannot accept one. A
read inside an atomic block is the exception, and the router's docstring says why.
"""

from typing import Any

from django.db import connections

PRIMARY = "default"
REPLICA = "replica"


class PrimaryReplicaRouter:
    """Route reads to the standby and writes to the primary.

    Inherits nothing; Django discovers routers by the methods they provide. It answers for every
    model, so it is the **last** entry in ``DATABASE_ROUTERS`` — Django takes the first non-null
    answer, so any narrower router must be listed ahead of this one to be reachable at all.

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
