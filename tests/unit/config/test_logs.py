"""Unit tests for the structured log formatter.

Covers the JSON document each log record becomes, including exception, stack, and caller-supplied
fields, so the collector shipping container output always receives one parsable record per line.
"""

import json
import logging
from pathlib import Path

import pytest

from config.logs import QueryRedactionFilter, StructuredFormatter

PROBE_LINE_NUMBER = 42
PROBE_DURATION = 0.004
PROBE_HASH = "argon2$argon2id$v=19$m=102400,t=2,p=8$c2FsdA$aGFzaA"


def _record(**overrides: object) -> logging.LogRecord:
    """Build a log record for formatting.

    Creates a record with fixed values and applies any overrides, so each test states only the
    attribute it cares about rather than repeating a full constructor call.

    Arguments:
        **overrides: Attributes to set on the record after construction.

    Returns:
        The constructed log record.

    Raises:
        None.
    """
    record = logging.LogRecord(
        name="localforge.probe",
        level=logging.WARNING,
        pathname=str(Path("src") / "config" / "logs.py"),
        lineno=PROBE_LINE_NUMBER,
        msg="serving %s",
        args=("a request",),
        exc_info=None,
    )

    for name, value in overrides.items():
        setattr(record, name, value)

    return record


@pytest.mark.unit
def test_a_record_renders_as_one_json_line() -> None:
    """Render a record as a single JSON line.

    Confirms the formatter emits one parsable JSON document carrying the identifying fields a log
    query needs, with the message already interpolated.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the output is not one line or omits an expected field.
    """
    rendered = StructuredFormatter().format(_record())
    payload = json.loads(rendered)

    assert "\n" not in rendered
    assert payload["level"] == "WARNING"
    assert payload["logger"] == "localforge.probe"
    assert payload["message"] == "serving a request"
    assert payload["line"] == PROBE_LINE_NUMBER
    assert payload["timestamp"].endswith("+00:00")


@pytest.mark.unit
def test_caller_supplied_fields_are_carried_through() -> None:
    """Carry extra fields onto the record.

    Confirms an attribute attached by the caller reaches the document, which is how a request
    identifier will travel with every record belonging to one request.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the extra field is absent from the document.
    """
    payload = json.loads(StructuredFormatter().format(_record(request_id="probe-identifier")))

    assert payload["request_id"] == "probe-identifier"


@pytest.mark.unit
def test_an_exception_is_rendered_into_the_document() -> None:
    """Keep a traceback inside the record.

    Confirms exception information is rendered into a field rather than printed as extra lines,
    since a multi-line traceback would otherwise break one-record-per-line collection.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the exception text is missing or the output spans several lines.
    """
    error = ValueError("probe failure")
    record = _record(exc_info=(type(error), error, error.__traceback__))

    rendered = StructuredFormatter().format(record)
    payload = json.loads(rendered)

    assert "\n" not in rendered
    assert "ValueError: probe failure" in payload["exception"]


@pytest.mark.unit
def test_stack_information_is_rendered_into_the_document() -> None:
    """Keep stack information inside the record.

    Confirms captured stack information reaches its own field, so a record logged with stack
    capture stays a single document.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the stack field is missing or does not carry the captured text.
    """
    payload = json.loads(StructuredFormatter().format(_record(stack_info="probe stack")))

    assert payload["stack"] == "probe stack"


@pytest.mark.unit
def test_a_caller_field_cannot_overwrite_a_fixed_field() -> None:
    """Keep the fields a log query depends on authoritative.

    Confirms an attribute named like one of the formatter's own fields does not replace it, since a
    caller able to rewrite the level or the timestamp would make every level-based query unsound.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If a caller-supplied value replaces a fixed field.
    """
    record = _record(level="spoofed", timestamp="spoofed", logger="spoofed")
    payload = json.loads(StructuredFormatter().format(record))

    assert payload["level"] == "WARNING"
    assert payload["logger"] == "localforge.probe"
    assert payload["timestamp"].endswith("+00:00")


@pytest.mark.unit
def test_an_unrepresentable_document_still_renders() -> None:
    """Never lose a record to serialisation.

    Confirms a payload JSON cannot represent at all, such as one containing a cycle or a key that
    is not a string, still produces one line rather than raising and having the record dropped.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the output is not one parsable line carrying the field.
    """
    cyclic: list[object] = []
    cyclic.append(cyclic)

    rendered = StructuredFormatter().format(_record(probe=cyclic, mapping={(1, 2): "value"}))
    payload = json.loads(rendered)

    assert "\n" not in rendered
    assert payload["level"] == "WARNING"
    assert "[...]" in payload["probe"]
    assert "(1, 2)" in payload["mapping"]


@pytest.mark.unit
def test_an_unserialisable_field_falls_back_to_its_text() -> None:
    """Never fail a log call on serialisation.

    Confirms a value JSON cannot represent is rendered as text instead of raising, because a
    formatter that raises loses the record it was asked to emit.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the value is not rendered as text.
    """
    payload = json.loads(StructuredFormatter().format(_record(probe=object())))

    assert payload["probe"].startswith("<object object at")


@pytest.mark.unit
def test_query_values_are_kept_out_of_the_log_stream() -> None:
    """Keep a credential out of a query record.

    Confirms the statement is reduced to its operation and table and the parameters are dropped,
    because an insert of an account carries its password hash in both the statement and the
    parameters, and the stream is shipped and retained.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If any part of the credential survives.
    """
    statement = " ".join(["INSERT INTO accounts_user (password) VALUES", f"('{PROBE_HASH}')"])
    record = _record(
        sql=statement,
        params=[PROBE_HASH],
        alias="default",
        duration=PROBE_DURATION,
    )

    assert QueryRedactionFilter().filter(record) is True

    payload = json.loads(StructuredFormatter().format(record))

    assert PROBE_HASH not in json.dumps(payload)
    assert payload["message"] == "INSERT INTO accounts_user (password)"
    assert payload["alias"] == "default"
    assert payload["duration"] == PROBE_DURATION


@pytest.mark.unit
def test_a_record_carrying_no_statement_is_left_alone() -> None:
    """Leave an ordinary record untouched.

    Confirms a record without a statement passes through unchanged, so attaching the filter to a
    handler cannot quietly rewrite messages that were never database queries.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the message is rewritten.
    """
    record = _record()

    assert QueryRedactionFilter().filter(record) is True
    assert json.loads(StructuredFormatter().format(record))["message"] == "serving a request"
