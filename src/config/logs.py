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
