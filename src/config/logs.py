"""Structured log formatting.

Renders every log record as a single JSON object on one line, so the collector that ships container
stdout into the log store can index fields rather than parse prose.
"""

import json
import logging
from datetime import UTC, datetime
from typing import override

RESERVED_ATTRIBUTES = frozenset(logging.makeLogRecord({}).__dict__) | {
    "asctime",
    "message",
    "taskName",
}

QUERY_ATTRIBUTES = ("sql", "params")
QUERY_SUMMARY_WORDS = 4
TASK_ARGUMENT_FIELDS = ("args", "kwargs")
REDACTED_ARGUMENTS = "********"


class TaskArgumentRedactionFilter(logging.Filter):
    """Keep task arguments out of the queue's own log records.

    Inherits from ``logging.Filter`` and blanks the argument fields the task queue attaches to the
    records it writes itself, because those are rendered from the call rather than from the
    project's own failure record and would otherwise carry whatever a caller passed.

    Attributes:
        None beyond those the base filter defines.

    Members:
        filter: Blank the argument fields on one queue record.
    """

    @override
    def filter(self, record: logging.LogRecord) -> bool:
        """Blank the argument fields on one queue record.

        Leaves the task's name and identifier in place, which is what makes a record traceable,
        and replaces only the rendered arguments, which the project's own failure record reports
        in a form that is safe by construction.

        Arguments:
            record: The log record to rewrite.

        Returns:
            True, always: the record is kept, only reduced.

        Raises:
            None.
        """
        data = getattr(record, "data", None)

        if isinstance(data, dict):
            for field in TASK_ARGUMENT_FIELDS:
                if field in data:
                    data[field] = REDACTED_ARGUMENTS

        return True


class QueryRedactionFilter(logging.Filter):
    """Keep query values out of the log stream.

    Inherits from ``logging.Filter`` and rewrites database records so a reader still sees which
    connection served which kind of statement, without the parameter values: an insert of an
    account carries its password hash twice, and the log stream is shipped and retained.

    Attributes:
        None beyond those the base filter defines.

    Members:
        filter: Reduce one database record to its statement summary.
    """

    @override
    def filter(self, record: logging.LogRecord) -> bool:
        """Reduce one database record to its statement summary.

        Replaces the interpolated statement with its leading words — the operation and the table —
        and drops the parameters, leaving the alias and the duration the record was logged for.

        Arguments:
            record: The log record to rewrite.

        Returns:
            True, always: the record is kept, only reduced.

        Raises:
            None.
        """
        statement = getattr(record, "sql", None)

        if isinstance(statement, str):
            record.msg = " ".join(statement.split()[:QUERY_SUMMARY_WORDS])
            record.args = ()

        for name in QUERY_ATTRIBUTES:
            if hasattr(record, name):
                delattr(record, name)

        return True


def _representable(value: object) -> object:
    """Reduce one value to something JSON can carry.

    Returns the value unchanged when it can be serialised on its own, and its representation when
    it cannot, so one unrepresentable attribute costs its own fidelity rather than the record.

    Arguments:
        value: The value to check.

    Returns:
        The value, or its representation when serialising it fails.

    Raises:
        None.
    """
    try:
        json.dumps(value, default=str)
    except TypeError, ValueError:
        return repr(value)

    return value


class StructuredFormatter(logging.Formatter):
    """Format log records as single-line JSON objects.

    Inherits from ``logging.Formatter`` and replaces its text rendering with a JSON document
    carrying the fields a log query needs, plus any extra attribute the caller attached to the
    record.

    Attributes:
        None beyond those the base formatter defines.

    Members:
        format: Render one record as a JSON document.
    """

    @override
    def format(self, record: logging.LogRecord) -> str:
        """Render one log record as a JSON document.

        Lays down any caller-supplied attributes first so the fields a log query depends on cannot
        be overwritten by one, appends exception and stack text, and falls back to a representation
        of each value when the document itself cannot be serialised.

        Arguments:
            record: The log record to render.

        Returns:
            The record as a single-line JSON document.

        Raises:
            None.
        """
        payload: dict[str, object] = {
            name: value
            for name, value in record.__dict__.items()
            if name not in RESERVED_ATTRIBUTES
        }

        payload.update(
            {
                "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
                "module": record.module,
                "line": record.lineno,
                "process": record.process,
                "thread": record.thread,
            }
        )

        if record.exc_info is not None:
            payload["exception"] = self.formatException(record.exc_info)

        if record.stack_info is not None:
            payload["stack"] = self.formatStack(record.stack_info)

        try:
            return json.dumps(payload, default=str)
        except TypeError, ValueError:
            return json.dumps(
                {name: _representable(value) for name, value in payload.items()},
                default=str,
            )
