"""Structured log formatting.

Renders every log record as a single JSON object on one line, so the collector that ships container
stdout into the log store can index fields rather than parse prose.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import traceback
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator, Mapping
from contextlib import ExitStack
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from http import HTTPStatus
from importlib import import_module
from typing import TYPE_CHECKING, cast, override
from urllib.parse import unquote_plus

from asgiref.sync import sync_to_async
from django.core.handlers.asgi import ASGIRequest
from django.db import DatabaseError
from django.http import HttpRequest, StreamingHttpResponse
from django.utils.decorators import async_only_middleware
from django.views.debug import SafeExceptionReporterFilter
from prometheus_client import Counter, Histogram

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Generator
    from types import TracebackType

    from asgiref.typing import (
        ASGI3Application,
        ASGIReceiveCallable,
        ASGIReceiveEvent,
        ASGISendCallable,
        ASGISendEvent,
        Scope,
    )
    from django.http import HttpResponseBase

RESERVED_ATTRIBUTES = frozenset(logging.makeLogRecord({}).__dict__) | {
    "asctime",
    "message",
    "taskName",
}

QUERY_ATTRIBUTES = ("sql", "params")
DATABASE_DIAGNOSTIC_FIELDS = frozenset({"error", "exc", "exception", "traceback"})
HEADER_PAIR_LENGTH = 2
QUOTED_FIELD_MINIMUM_LENGTH = 2
TASK_ARGUMENT_FIELDS = ("args", "kwargs")
MAXIMUM_FIELD_NAME_CHARACTERS = 128
REDACTED_ARGUMENTS = "********"
REQUEST_ID_HEADER = "X-Request-ID"
NO_REQUEST_ID = "-"
REQUEST_ID_META_KEY = "localforge.request_id"
REQUEST_ID_EXCEPTION_ATTRIBUTE = "localforge_request_id"
REPEATED_REFERENCE = "<repeated reference>"
INVALID_HTTP_METHOD = "<invalid method>"
STREAM_END = object()
HTTP_METHODS = frozenset(
    {
        "CONNECT",
        "DELETE",
        "GET",
        "HEAD",
        "OPTIONS",
        "PATCH",
        "POST",
        "PUT",
        "TRACE",
    }
)
SQL_OPERATIONS = frozenset(
    {
        "DELETE",
        "INSERT",
        "MERGE",
        "SELECT",
        "UPDATE",
        "WITH",
    }
)
SENSITIVE_FIELD_FRAGMENTS = (
    "access_key",
    "api_key",
    "authorization",
    "basic_auth",
    "cookie",
    "credential",
    "password",
    "private_key",
    "secret",
    "session_key",
    "sessionid",
    "signing_key",
    "token",
)
SENSITIVE_FIELD_TOKENS = frozenset(
    {"auth", "credential", "pass", "passwd", "password", "secret", "token"}
)
SENSITIVE_FIELD_NAMES = frozenset(
    {
        "celery_broker_url",
        "celery_result_backend",
        "django_secret_key",
        "flower_basic_auth",
        "grafana_admin_password",
        "pgadmin_default_password",
        "postgres_password",
        "postgres_replication_password",
        "rabbitmq_default_pass",
        "s3_access_key_id",
        "s3_secret_access_key",
        "traefik_dashboard_auth",
        "traefik_dashboard_password",
        "valkey_cache_password",
        "valkey_channels_password",
    }
)
HEADER_ASSIGNMENT = re.compile(
    r"(?im)\b(authorization|cookie)((?>\s*)[\"']?(?>\s*)[:=](?>\s*))[^\r\n]*"
)
FOLLOWING_ASSIGNMENT = re.compile(
    r"""[ \t]*(?:(?:[A-Za-z0-9_. /-]+)|"""
    r"""(?:[A-Za-z0-9_. /-]*\[\s*['"][^'"\r\n]+['"]\s*\]))[ \t]*[:=]"""
)
QUERY_PARAMETER = re.compile(r"([?&])([^?=&\s]+)=([^&#\s]*)")
URI_CREDENTIAL = re.compile(r"(://)([^/@\s]+)(@)")
request_identifier: ContextVar[str] = ContextVar("request_identifier", default=NO_REQUEST_ID)
request_logger = logging.getLogger("localforge.request")
http_responses = Counter(
    "localforge_http_responses_total",
    "Completed HTTP responses at the outer application boundary.",
    ("method", "status", "view"),
)
http_request_duration = Histogram(
    "localforge_http_request_duration_seconds",
    "HTTP request duration at the outer application boundary.",
    ("method", "status", "view"),
)


@lru_cache(maxsize=1024)
def _is_sensitive_field_name(lowered: str) -> bool:
    """Classify one normalized field name as sensitive.

    Caches repeated names encountered while scanning structured records and assignment-like text,
    preserving the complete credential vocabulary with bounded memory use.

    Arguments:
        lowered: Lowercase candidate field name.

    Returns:
        True when the field names credential-bearing content.

    Raises:
        None.
    """
    normalized = re.sub(r"[^a-z0-9]", "", lowered)
    tokens = frozenset(re.findall(r"[a-z0-9]+", lowered))

    return (
        lowered in SENSITIVE_FIELD_NAMES
        or SafeExceptionReporterFilter.hidden_settings.search(lowered) is not None
        or bool(tokens & SENSITIVE_FIELD_TOKENS)
        or any(fragment.replace("_", "") in normalized for fragment in SENSITIVE_FIELD_FRAGMENTS)
    )


def _is_sensitive_field(name: object) -> bool:
    """Identify a field whose value must not enter the log stream.

    Normalizes mapping keys and log-record attribute names before matching the credential-bearing
    fragments the platform forbids, so nested structures receive the same treatment as top-level
    fields.

    Arguments:
        name: Candidate field name.

    Returns:
        True when the field names credential-bearing content.

    Raises:
        None.
    """
    return _is_sensitive_field_name(str(name).lower())


def _is_sensitive_pair(items: list[object]) -> bool:
    """Identify a two-item sequence representing a sensitive field and value.

    Restricts field detection to text and byte names so an ordinary two-item collection whose
    first value merely contains credential-bearing data is not misclassified as a header pair.

    Arguments:
        items: Sequence values to inspect.

    Returns:
        True when the values form a sensitive name and value pair.

    Raises:
        None.
    """
    return (
        len(items) == HEADER_PAIR_LENGTH
        and isinstance(items[0], str | bytes)
        and _is_sensitive_field(items[0])
    )


def _database_exception(error: BaseException) -> BaseException | None:
    """Find a database exception in one causal exception chain.

    Traverses explicit causes before implicit contexts while guarding against cycles, allowing
    driver diagnostics wrapped by application exceptions to receive database-safe rendering.

    Arguments:
        error: Exception whose causal chain should be inspected.

    Returns:
        First database exception in the chain, or None.

    Raises:
        None.
    """
    current: BaseException | None = error
    seen: set[int] = set()

    while current is not None and id(current) not in seen:
        if isinstance(current, DatabaseError):
            return current

        seen.add(id(current))
        current = current.__cause__ or current.__context__

    return None


def _format_database_exception(
    error: BaseException,
    trace: TracebackType | None,
) -> str:
    """Render database failure structure without driver diagnostic values.

    Keeps the safe exception type, validated SQLSTATE, and stack locations while excluding messages,
    statement excerpts, detail fields, and bound values supplied by the database driver.

    Arguments:
        error: Database exception selected from the causal chain.
        trace: Traceback attached to the emitted log record.

    Returns:
        Safe multiline exception summary.

    Raises:
        None.
    """
    exception_name = f"{type(error).__module__}.{type(error).__qualname__}"
    sqlstate = getattr(error, "sqlstate", None) or getattr(error.__cause__, "sqlstate", None)
    summary = f"{exception_name}: database operation failed"

    if isinstance(sqlstate, str) and re.fullmatch(r"[A-Z0-9]{5}", sqlstate):
        summary = f"{summary} (SQLSTATE {sqlstate})"

    frames = traceback.extract_tb(trace)
    locations = [
        f'  File "{frame.filename}", line {frame.lineno}, in {frame.name}' for frame in frames
    ]

    return "\n".join([*locations, summary])


def _replace_database_diagnostics(
    value: object,
    safe_exception: str,
    *,
    field_name: object = "",
    seen: set[int] | None = None,
) -> object:
    """Replace copied database diagnostics with one safe exception summary.

    Traverses structured logging extras used by task workers, replacing exception and traceback
    fields that duplicate database-provided messages outside the standard ``exc_info`` attribute.

    Arguments:
        value: Log value that may contain copied diagnostics.
        safe_exception: Reduced database exception rendering.
        field_name: Name associated with the current value.
        seen: Container identities already visited during traversal.

    Returns:
        Value with database diagnostic fields replaced.

    Raises:
        None.
    """
    if str(field_name).lower() in DATABASE_DIAGNOSTIC_FIELDS:
        return safe_exception

    if isinstance(value, BaseException) and _database_exception(value) is not None:
        return safe_exception

    if not isinstance(value, Mapping | list | tuple):
        return value

    visited = seen if seen is not None else set()
    identity = id(value)

    if identity in visited:
        return REPEATED_REFERENCE
    visited.add(identity)

    if isinstance(value, Mapping):
        return {
            str(name): _replace_database_diagnostics(
                item,
                safe_exception,
                field_name=name,
                seen=visited,
            )
            for name, item in value.items()
        }

    replaced_items = [
        _replace_database_diagnostics(item, safe_exception, seen=visited) for item in value
    ]

    return tuple(replaced_items) if isinstance(value, tuple) else replaced_items


def _format_database_log_message(
    record: logging.LogRecord,
    safe_exception: str,
) -> str:
    """Render a database failure message without interpolating driver diagnostics.

    Replaces exception-bearing logging arguments before applying the original message template,
    preserving safe task identity fields while preventing copied database values from entering the
    final message.

    Arguments:
        record: Log record carrying the message template and interpolation arguments.
        safe_exception: Reduced database exception rendering.

    Returns:
        Safely interpolated message or a fixed database failure summary.

    Raises:
        None.
    """
    if not record.args:
        return "database operation failed"

    arguments = _replace_database_diagnostics(record.args, safe_exception)

    try:
        return str(record.msg) % arguments
    except (
        KeyError,
        TypeError,
        ValueError,
    ):
        return "database operation failed"


def _redact_text(value: str) -> str:
    """Remove recognizable credential assignments from text.

    Rewrites named assignments and URI passwords while leaving surrounding diagnostic text intact,
    so records remain useful without preserving the sensitive value.

    Arguments:
        value: Text that may contain credential material.

    Returns:
        Text with recognized credential values replaced.

    Raises:
        None.
    """
    query_parameters_removed = QUERY_PARAMETER.sub(
        lambda match: (
            f"{match.group(1)}{match.group(2)}={REDACTED_ARGUMENTS}"
            if _is_sensitive_field(unquote_plus(match.group(2)))
            else match.group(0)
        ),
        value,
    )
    headers_removed = HEADER_ASSIGNMENT.sub(
        lambda match: f"{match.group(1)}{match.group(2)}{REDACTED_ARGUMENTS}",
        query_parameters_removed,
    )
    assignments_removed = _redact_assignments(headers_removed)

    return URI_CREDENTIAL.sub(
        lambda match: f"{match.group(1)}{REDACTED_ARGUMENTS}{match.group(3)}",
        assignments_removed,
    )


def _assignment_field(value: str, separator: int, lower_bound: int) -> str:
    """Read the bounded field name preceding an assignment separator.

    Skips intervening whitespace and scans only accepted identifier characters within the maximum
    field-name length.

    Arguments:
        value: Text containing the assignment.
        separator: Index of the assignment separator.
        lower_bound: Earliest index not already copied to output.

    Returns:
        Candidate assignment field name.

    Raises:
        None.
    """
    field_end = separator
    while field_end > lower_bound and value[field_end - 1].isspace():
        field_end -= 1
    if field_end > lower_bound and value[field_end - 1] in {"'", '"'}:
        field_end -= 1

    if field_end > lower_bound and value[field_end - 1] == "]":
        bracket_start = value.rfind(
            "[",
            max(lower_bound, field_end - MAXIMUM_FIELD_NAME_CHARACTERS),
            field_end,
        )
        if bracket_start >= lower_bound:
            bracketed_field = value[bracket_start + 1 : field_end - 1].strip()
            if (
                len(bracketed_field) >= QUOTED_FIELD_MINIMUM_LENGTH
                and bracketed_field[0] in {"'", '"'}
                and bracketed_field[-1] == bracketed_field[0]
            ):
                return bracketed_field[1:-1]

    field_start = field_end
    remaining = MAXIMUM_FIELD_NAME_CHARACTERS
    while (
        field_start > lower_bound
        and remaining > 0
        and (value[field_start - 1].isalnum() or value[field_start - 1] in "_. /-")
    ):
        field_start -= 1
        remaining -= 1

    return value[field_start:field_end]


def _assignment_block_end(value: str, separator: int, value_start: int) -> int | None:
    """Find the end of an indented multiline assignment.

    Recognizes YAML literal and folded markers plus implicit newline continuations, consuming only
    lines indented beyond the sensitive field's containing level.

    Arguments:
        value: Text containing the assignment.
        separator: Index of the assignment separator.
        value_start: Index of the first non-whitespace value character.

    Returns:
        End of the multiline value, or ``None`` for a single-line assignment.

    Raises:
        None.
    """
    line_start = value.rfind("\n", 0, separator) + 1
    line_prefix = value[line_start:separator]
    base_indentation = len(line_prefix) - len(line_prefix.lstrip())
    prefix = value[separator + 1 : value_start]
    uses_block_marker = value_start < len(value) and value[value_start] in {"|", ">"}

    if not uses_block_marker and "\n" not in prefix:
        return None

    if uses_block_marker:
        marker_line_end = value.find("\n", value_start)
        if marker_line_end < 0:
            return len(value)
        scan_start = marker_line_end + 1
        value_end = marker_line_end
    else:
        scan_start = value.rfind("\n", separator + 1, value_start) + 1
        value_end = value_start

    consume_first_line = not uses_block_marker
    while scan_start < len(value):
        line_end = value.find("\n", scan_start)
        if line_end < 0:
            line_end = len(value)
        line = value[scan_start:line_end]
        indentation = len(line) - len(line.lstrip())

        if line.strip() and indentation <= base_indentation and not consume_first_line:
            break

        value_end = line_end
        scan_start = line_end + 1
        consume_first_line = False

    return value_end


def _assignment_value_end(value: str, separator: int, value_start: int) -> int:
    """Find the end of one quoted or unquoted assignment value.

    Handles escaped quote characters and indented multiline blocks while keeping all scans
    forward-only and bounded by the input length.

    Arguments:
        value: Text containing the assignment value.
        separator: Index of the assignment separator.
        value_start: Index of the first non-whitespace value character.

    Returns:
        Index immediately after the assignment value.

    Raises:
        None.
    """
    block_end = _assignment_block_end(value, separator, value_start)
    if block_end is not None:
        return block_end

    value_end = value_start
    if value_start >= len(value) or value[value_start] not in {"'", '"'}:
        while value_end < len(value) and value[value_end] not in "\r\n":
            if (
                value[value_end] in "&;,}]"
                and FOLLOWING_ASSIGNMENT.match(value, value_end + 1) is not None
            ):
                break
            value_end += 1

        return value_end

    quote = value[value_start]
    value_end += 1
    escaped = False
    while value_end < len(value):
        character = value[value_end]
        value_end += 1
        if character == quote and not escaped:
            return value_end
        escaped = character == "\\" and not escaped
        if character != "\\":
            escaped = False

    return value_end


def _redact_assignments(value: str) -> str:
    """Remove sensitive named assignments with bounded linear scanning.

    Examines each assignment separator once and looks backward through at most one field-name
    budget, preventing diagnostic prefixes or adversarial punctuation from hiding nested secrets.

    Arguments:
        value: Text whose named assignments should be sanitized.

    Returns:
        Text with sensitive assignment values replaced.

    Raises:
        None.
    """
    result: list[str] = []
    emitted_until = 0
    index = 0

    while index < len(value):
        if value[index] not in {":", "="}:
            index += 1
            continue

        field_name = _assignment_field(value, index, emitted_until)
        if not field_name or not _is_sensitive_field(field_name):
            index += 1
            continue

        value_start = index + 1
        if value_start < len(value) and value[value_start] == ">":
            value_start += 1
        while value_start < len(value) and value[value_start].isspace():
            value_start += 1

        value_end = _assignment_value_end(value, index, value_start)
        result.extend((value[emitted_until:value_start], REDACTED_ARGUMENTS))
        emitted_until = value_end
        index = value_end

    result.append(value[emitted_until:])

    return "".join(result)


def _redact_value(
    value: object,
    *,
    field_name: object = "",
    seen: set[int] | None = None,
) -> object:
    """Redact sensitive fields throughout one log value.

    Traverses mappings and sequences while guarding against cycles, replacing a whole value when
    its field name is sensitive and sanitizing recognizable assignments inside ordinary strings.

    Arguments:
        value: Value to sanitize.
        field_name: Name associated with the value, when one exists.
        seen: Object identifiers already visited during this traversal.

    Returns:
        A log-safe representation of the value.

    Raises:
        None.
    """
    if _is_sensitive_field(field_name):
        return REDACTED_ARGUMENTS

    redacted: object = value

    if isinstance(value, str):
        redacted = _redact_text(value)
    elif isinstance(value, HttpRequest):
        redacted = {
            "method": value.method,
            "path": _redact_text(value.path),
        }
    elif isinstance(value, Mapping | list | tuple):
        visited = seen if seen is not None else set()
        identity = id(value)

        if identity in visited:
            return REPEATED_REFERENCE
        visited.add(identity)

        if isinstance(value, Mapping):
            redacted = {
                str(name): _redact_value(item, field_name=name, seen=visited)
                for name, item in value.items()
            }
        else:
            items = list(value)

            if _is_sensitive_pair(items):
                redacted = [
                    _redact_value(items[0], seen=visited),
                    REDACTED_ARGUMENTS,
                ]
            else:
                redacted = [_redact_value(item, seen=visited) for item in items]
    elif value is not None and not isinstance(value, bool | int | float):
        try:
            redacted = _redact_text(repr(value))
        except (
            TypeError,
            ValueError,
        ):
            redacted = "<unrepresentable>"

    return redacted


class RequestContextFilter(logging.Filter):
    """Attach the active request identifier to every emitted record.

    Inherits from ``logging.Filter`` and reads the request-local context variable, giving all log
    calls made while one request is executing the same correlation value without changing their
    call sites.

    Attributes:
        None beyond those the base filter defines.

    Members:
        filter: Attach the active identifier to one record.
    """

    @override
    def filter(self, record: logging.LogRecord) -> bool:
        """Attach the active identifier to one record.

        Uses a fixed sentinel outside request handling, so every structured record carries the
        field and Loki queries never need to account for a missing key.

        Arguments:
            record: Log record to enrich.

        Returns:
            True, always: the enriched record is retained.

        Raises:
            None.
        """
        identifier = request_identifier.get()
        request = getattr(record, "request", None)
        metadata = getattr(request, "META", None)
        exception = record.exc_info[1] if record.exc_info is not None else None

        if identifier == NO_REQUEST_ID and isinstance(metadata, Mapping):
            request_identifier_from_record = metadata.get(REQUEST_ID_META_KEY)

            if isinstance(request_identifier_from_record, str):
                identifier = request_identifier_from_record

        if identifier == NO_REQUEST_ID and exception is not None:
            exception_identifier = getattr(exception, REQUEST_ID_EXCEPTION_ATTRIBUTE, None)

            if isinstance(exception_identifier, str):
                identifier = exception_identifier

        record.request_id = identifier

        return True


@dataclass(frozen=True, slots=True)
class _ResponseMetadata:
    """Carry body-free fields needed for terminal request observability.

    Groups request and response labels that always travel together through application and
    transport boundaries.

    Attributes:
        method: HTTP request method.
        path: Request path.
        status: Response status.
        view: Resolved view name or unresolved sentinel.
        started: Monotonic request start time.
        identifier: Correlation value returned to the caller.

    Members:
        None.
    """

    method: str
    path: str
    status: int
    view: str
    started: float
    identifier: str


def _record_response_completion(
    metadata: _ResponseMetadata,
    *,
    failed: bool,
) -> None:
    """Record terminal response metrics and one correlated log event.

    Accepts transport-level metadata so requests rejected before Django constructs an
    ``HttpRequest`` receive the same bounded metrics and correlation as ordinary responses.

    Arguments:
        metadata: Body-free request and response fields.
        failed: Whether body delivery failed or was interrupted.

    Returns:
        None.

    Raises:
        None.
    """
    duration = max(0.0, time.perf_counter() - metadata.started)
    metric_method = metadata.method if metadata.method in HTTP_METHODS else INVALID_HTTP_METHOD
    http_responses.labels(
        method=metric_method,
        status=str(metadata.status),
        view=metadata.view,
    ).inc()
    http_request_duration.labels(
        method=metric_method,
        status=str(metadata.status),
        view=metadata.view,
    ).observe(duration)
    log_method = request_logger.warning if failed else request_logger.info
    log_method(
        "request stream failed" if failed else "request completed",
        extra={
            "duration_seconds": duration,
            "http_method": metadata.method,
            "http_path": metadata.path,
            "http_status": metadata.status,
        },
    )


class _RequestFinalizer:
    """Finalize one response exactly once.

    Owns body-free response metadata needed for terminal metrics and logging while activating
    correlation only for the duration of that operation.

    Attributes:
        metadata: Body-free request and response fields.
        defer_success: Whether the ASGI send boundary owns successful completion.
        asynchronous_cleanup: Awaitable source cleanup registered by an asynchronous stream.
        resource_closers: Response cleanup callbacks retained for correlated execution.
        finalized: Whether a terminal record has already been emitted.

    Members:
        complete: Emit the terminal record once.
        close_resources: Run retained response cleanup once.
    """

    def __init__(
        self,
        metadata: _ResponseMetadata,
    ) -> None:
        """Capture one request's finalization state.

        Stores only body-free metadata, allowing application, transport, or interruption exits to
        complete the same metrics and logging operation.

        Arguments:
            metadata: Body-free request and response fields.

        Returns:
            None.

        Raises:
            None.
        """
        self.metadata = metadata
        self.defer_success = False
        self.asynchronous_cleanup: Callable[[], Awaitable[None]] | None = None
        self.resource_closers: list[Callable[[], object]] = []
        self.finalized = False

    def complete(self, *, failed: bool) -> None:
        """Record response completion once inside its request context.

        Restores the caller's current correlation value without relying on a context token, so
        disconnect cleanup remains safe when another task or thread closes the iterator.

        Arguments:
            failed: Whether body iteration failed or was interrupted.

        Returns:
            None.

        Raises:
            None.
        """
        if self.finalized:
            return

        previous_identifier = request_identifier.get()
        request_identifier.set(self.metadata.identifier)
        try:
            _record_response_completion(
                self.metadata,
                failed=failed,
            )
        finally:
            request_identifier.set(previous_identifier)
            self.finalized = True

    def close_resources(self) -> None:
        """Run retained response cleanup once inside request correlation.

        Executes Django's original stream and response closers under the request identifier so logs
        from generator ``finally`` blocks remain attributable during disconnect cleanup.

        Arguments:
            None.

        Returns:
            None.

        Raises:
            None.
        """
        resource_closers, self.resource_closers = self.resource_closers, []
        previous_identifier = request_identifier.get()
        request_identifier.set(self.metadata.identifier)
        try:
            with ExitStack() as stack:
                for close in reversed(resource_closers):
                    stack.callback(close)
        finally:
            request_identifier.set(previous_identifier)


@dataclass
class _ASGIFinalizerHolder:
    """Share a request finalizer across Django's ASGI child-task boundary.

    Stores mutable state in an object inherited through context copying, allowing the outer
    transport task to observe middleware registration before any response event is sent.

    Attributes:
        finalizer: Request finalizer published by the Django middleware chain.

    Members:
        None.
    """

    finalizer: _RequestFinalizer | None = None


asgi_finalizer_holder: ContextVar[_ASGIFinalizerHolder | None] = ContextVar(
    "asgi_finalizer_holder",
    default=None,
)
asgi_stream_transport_active: ContextVar[bool] = ContextVar(
    "asgi_stream_transport_active",
    default=False,
)
asgi_request_started: ContextVar[float | None] = ContextVar(
    "asgi_request_started",
    default=None,
)


class _ClosableSynchronousContent:
    """Expose correlated synchronous content with reliable cleanup.

    Wraps the correlated iterator with a ``close`` member that finalizes requests even when Django
    closes the response before body iteration begins.

    Attributes:
        content: Correlated synchronous response iterator.
        finalizer: Terminal metrics and logging operation.

    Members:
        __iter__: Return this synchronous iterator.
        __next__: Yield the next correlated body chunk.
        close: Finalize interruption and close the iterator.
    """

    def __init__(
        self,
        content: Generator[bytes],
        finalizer: _RequestFinalizer,
    ) -> None:
        """Capture synchronous content and its terminal operation.

        Stores both objects so response cleanup has a direct finalization path independent of
        whether Python entered the generator body.

        Arguments:
            content: Correlated synchronous response iterator.
            finalizer: Terminal metrics and logging operation.

        Returns:
            None.

        Raises:
            None.
        """
        self.content = content
        self.finalizer = finalizer

    def __iter__(self) -> _ClosableSynchronousContent:
        """Return this synchronous iterator.

        Keeps Django's streaming assignment bound to the object whose cleanup member owns terminal
        request finalization.

        Arguments:
            None.

        Returns:
            This synchronous iterator.

        Raises:
            None.
        """
        return self

    def __next__(self) -> bytes:
        """Yield the next correlated response body chunk.

        Delegates iteration without adding another generator boundary, preserving the correlated
        iterator's normal completion and failure behavior.

        Arguments:
            None.

        Returns:
            Next response body chunk.

        Raises:
            StopIteration: When the response body is exhausted.
            BaseException: Any failure raised by response body iteration.
        """
        return next(self.content)

    def close(self) -> None:
        """Finalize an interrupted response and close its iterator.

        Records failure before invoking iterator cleanup, ensuring an unstarted generator cannot
        bypass terminal metrics and logging.

        Arguments:
            None.

        Returns:
            None.

        Raises:
            None.
        """
        self.finalizer.complete(failed=True)
        self.finalizer.close_resources()
        previous_identifier = request_identifier.get()
        request_identifier.set(self.finalizer.metadata.identifier)
        try:
            self.content.close()
        finally:
            request_identifier.set(previous_identifier)


async def _close_asynchronous_iterator(iterator: object) -> None:
    """Close an asynchronous iterator when it exposes optional cleanup.

    Supports Django's full asynchronous-iterator contract without assuming the source is an async
    generator, while awaiting generator cleanup when that extension is available.

    Arguments:
        iterator: Asynchronous iterator or wrapper to close.

    Returns:
        None.

    Raises:
        BaseException: Any failure raised by the iterator's cleanup operation.
    """
    close = getattr(iterator, "aclose", None)

    if not callable(close):
        return

    result = close()
    if isinstance(result, Awaitable):
        await result


class _AsynchronousSourceCloser:
    """Close one asynchronous source at most once.

    Shares ownership between the correlation generator and outer response lifecycle so optional
    non-idempotent ``aclose`` implementations cannot receive duplicate cleanup calls.

    Attributes:
        source: Original asynchronous response iterator.
        closed: Whether source cleanup has already started.

    Members:
        close: Close the source once.
    """

    def __init__(self, source: AsyncIterator[bytes]) -> None:
        """Capture the asynchronous source.

        Starts with cleanup available so whichever lifecycle boundary observes interruption first
        becomes its sole owner.

        Arguments:
            source: Original asynchronous response iterator.

        Returns:
            None.

        Raises:
            None.
        """
        self.source = source
        self.closed = False

    async def close(self) -> None:
        """Close the source once.

        Marks ownership before awaiting optional cleanup so concurrent lifecycle paths cannot both
        invoke a non-idempotent close extension.

        Arguments:
            None.

        Returns:
            None.

        Raises:
            BaseException: Any failure raised by the source cleanup operation.
        """
        if self.closed:
            return

        self.closed = True
        await _close_asynchronous_iterator(self.source)


class _ClosableAsynchronousContent:
    """Expose correlated asynchronous content with synchronous cleanup.

    Captures the serving event loop and presents a ``close`` member Django can invoke from its
    synchronous response cleanup path without blocking the thread-sensitive executor.

    Attributes:
        content: Correlated asynchronous response generator.
        source_closer: Shared owner of original asynchronous source cleanup.
        finalizer: Terminal metrics and logging operation.
        loop: Event loop serving the response.
        cleanup_task: Serving-loop task that owns asynchronous source cleanup.

    Members:
        __aiter__: Return this asynchronous iterator.
        __anext__: Yield the next correlated body chunk.
        close: Schedule asynchronous generator closure.
        _schedule_cleanup: Create or return the serving-loop cleanup task.
        _wait_for_cleanup: Await deterministic asynchronous source cleanup.
    """

    def __init__(
        self,
        content: AsyncGenerator[bytes],
        source_closer: _AsynchronousSourceCloser,
        finalizer: _RequestFinalizer,
    ) -> None:
        """Capture asynchronous content and its serving loop.

        Stores the generator before Django transfers cleanup to a synchronous worker thread at the
        end of the ASGI response lifecycle.

        Arguments:
            content: Correlated asynchronous response generator.
            source_closer: Shared owner of original asynchronous source cleanup.
            finalizer: Terminal metrics and logging operation.

        Returns:
            None.

        Raises:
            RuntimeError: If constructed outside a running event loop.
        """
        self.content = content
        self.source_closer = source_closer
        self.finalizer = finalizer
        self.loop = asyncio.get_running_loop()
        self.cleanup_task: asyncio.Task[None] | None = None
        self.finalizer.asynchronous_cleanup = self._wait_for_cleanup

    def __aiter__(self) -> _ClosableAsynchronousContent:
        """Return this asynchronous response iterator.

        Allows Django to consume the wrapper directly without introducing another generator that
        could intercept cleanup.

        Arguments:
            None.

        Returns:
            This asynchronous iterator.

        Raises:
            None.
        """
        return self

    async def __anext__(self) -> bytes:
        """Yield the next correlated response body chunk.

        Delegates advancement to the correlated generator while retaining the synchronous cleanup
        member Django records when assigning streaming content.

        Arguments:
            None.

        Returns:
            Next response body chunk.

        Raises:
            StopAsyncIteration: When the response body is exhausted.
            BaseException: Any failure raised by response body iteration.
        """
        return await anext(self.content)

    def close(self) -> None:
        """Close asynchronous content on its serving loop.

        Finalizes interruption before scheduling source cleanup without occupying Django's
        thread-sensitive executor while asynchronous finalizers may need that same executor.

        Arguments:
            None.

        Returns:
            None.

        Raises:
            None.
        """
        self.finalizer.complete(failed=True)
        self.finalizer.close_resources()
        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None

        if running_loop is self.loop:
            self._schedule_cleanup()
            return

        self.loop.call_soon_threadsafe(self._schedule_cleanup)

    def _schedule_cleanup(self) -> asyncio.Task[None]:
        """Create or return the asynchronous cleanup task.

        Runs only on the serving event loop, giving synchronous response cleanup a non-blocking
        bridge while the outer ASGI lifecycle retains an awaitable completion handle.

        Arguments:
            None.

        Returns:
            Task that closes the correlated and original asynchronous iterators.

        Raises:
            RuntimeError: If the serving event loop cannot create the cleanup task.
        """
        if self.cleanup_task is None:
            self.cleanup_task = self.loop.create_task(self._close_content())

        return self.cleanup_task

    async def _wait_for_cleanup(self) -> None:
        """Await asynchronous source cleanup on the serving loop.

        Creates cleanup when Django did not invoke its synchronous close callback, then propagates
        any source-finalizer failure through the outer ASGI lifecycle.

        Arguments:
            None.

        Returns:
            None.

        Raises:
            BaseException: Any failure raised while closing asynchronous source content.
        """
        await self._schedule_cleanup()

    async def _close_content(self) -> None:
        """Close asynchronous content inside request correlation.

        Activates the request identifier in the serving loop before generator cleanup begins,
        preserving attribution for logs emitted from source ``finally`` blocks.

        Arguments:
            None.

        Returns:
            None.

        Raises:
            None.
        """
        previous_identifier = request_identifier.get()
        request_identifier.set(self.finalizer.metadata.identifier)
        try:
            await _close_asynchronous_iterator(self.content)
            await self.source_closer.close()
        finally:
            request_identifier.set(previous_identifier)


class _ClosableSynchronousASGIContent:
    """Expose synchronous content as a closeable asynchronous iterator.

    Advances each chunk in Django's thread-sensitive executor while retaining a synchronous close
    path that cannot deadlock by submitting cleanup back to its occupied worker.

    Attributes:
        content: Correlated synchronous response generator.
        finalizer: Terminal metrics and logging operation.

    Members:
        __aiter__: Return this asynchronous iterator.
        __anext__: Advance the synchronous source in its executor.
        close: Finalize interruption and close the source directly.
    """

    def __init__(
        self,
        content: Generator[bytes],
        finalizer: _RequestFinalizer,
    ) -> None:
        """Capture synchronous ASGI content and its terminal operation.

        Stores the generator directly so Django's response cleanup worker owns source closure
        without an asynchronous scheduling cycle.

        Arguments:
            content: Correlated synchronous response generator.
            finalizer: Terminal metrics and logging operation.

        Returns:
            None.

        Raises:
            None.
        """
        self.content = content
        self.finalizer = finalizer

    def __aiter__(self) -> _ClosableSynchronousASGIContent:
        """Return this asynchronous iterator.

        Allows Django to consume one synchronous chunk per outbound ASGI body event while the
        wrapper retains direct cleanup ownership.

        Arguments:
            None.

        Returns:
            This asynchronous iterator.

        Raises:
            None.
        """
        return self

    async def __anext__(self) -> bytes:
        """Advance the synchronous source in Django's thread-sensitive executor.

        Converts the private exhaustion sentinel into the asynchronous iterator protocol without
        pre-consuming later chunks before transport delivery.

        Arguments:
            None.

        Returns:
            Next response body chunk.

        Raises:
            StopAsyncIteration: When the synchronous body is exhausted.
            BaseException: Any synchronous body iteration failure.
        """
        chunk = await sync_to_async(
            _next_synchronous_chunk,
            thread_sensitive=True,
        )(self.content)

        if chunk is STREAM_END:
            raise StopAsyncIteration

        return cast("bytes", chunk)

    def close(self) -> None:
        """Finalize interruption and close the synchronous source.

        Runs directly in Django's response cleanup worker, avoiding a circular wait on that same
        thread-sensitive executor.

        Arguments:
            None.

        Returns:
            None.

        Raises:
            None.
        """
        self.finalizer.complete(failed=True)
        self.finalizer.close_resources()
        previous_identifier = request_identifier.get()
        request_identifier.set(self.finalizer.metadata.identifier)
        try:
            self.content.close()
        finally:
            request_identifier.set(previous_identifier)


async def _correlate_asynchronous_content(
    content: AsyncIterator[bytes],
    finalizer: _RequestFinalizer,
    source_closer: _AsynchronousSourceCloser,
) -> AsyncGenerator[bytes]:
    """Yield an asynchronous body with per-iteration correlation.

    Activates the request identifier only while advancing the underlying iterator, then finalizes
    normally on exhaustion or as a failure on an exception, cancellation, or disconnect close.

    Arguments:
        content: Original asynchronous response iterator.
        finalizer: Terminal metrics and logging operation.
        source_closer: Shared owner of original asynchronous source cleanup.

    Returns:
        Asynchronous iterator yielding the original response body chunks.

    Yields:
        Response body chunks from the original asynchronous iterator.

    Raises:
        BaseException: Any failure raised by the original iterator or caller interruption.
    """
    try:
        while True:
            previous_identifier = request_identifier.get()
            request_identifier.set(finalizer.metadata.identifier)
            try:
                chunk = await anext(content)
            except StopAsyncIteration:
                if not finalizer.defer_success:
                    finalizer.complete(failed=False)
                return
            except BaseException as error:
                setattr(error, REQUEST_ID_EXCEPTION_ATTRIBUTE, finalizer.metadata.identifier)
                finalizer.complete(failed=True)
                raise
            finally:
                request_identifier.set(previous_identifier)

            yield chunk
    except BaseException as error:
        setattr(error, REQUEST_ID_EXCEPTION_ATTRIBUTE, finalizer.metadata.identifier)
        finalizer.complete(failed=True)
        previous_identifier = request_identifier.get()
        request_identifier.set(finalizer.metadata.identifier)
        try:
            await source_closer.close()
        finally:
            request_identifier.set(previous_identifier)
        raise


def _correlate_synchronous_content(
    content: Iterator[bytes],
    finalizer: _RequestFinalizer,
) -> Generator[bytes]:
    """Yield a synchronous body with per-iteration correlation.

    Activates the request identifier only while advancing the underlying iterator, then finalizes
    normally on exhaustion or as a failure on an exception or caller interruption.

    Arguments:
        content: Original synchronous response iterator.
        finalizer: Terminal metrics and logging operation.

    Returns:
        Iterator yielding the original response body chunks.

    Yields:
        Response body chunks from the original synchronous iterator.

    Raises:
        BaseException: Any failure raised by the original iterator or caller interruption.
    """
    try:
        while True:
            previous_identifier = request_identifier.get()
            request_identifier.set(finalizer.metadata.identifier)
            try:
                chunk = next(content)
            except StopIteration:
                if not finalizer.defer_success:
                    finalizer.complete(failed=False)
                return
            except BaseException as error:
                setattr(error, REQUEST_ID_EXCEPTION_ATTRIBUTE, finalizer.metadata.identifier)
                finalizer.complete(failed=True)
                raise
            finally:
                request_identifier.set(previous_identifier)

            yield chunk
    except BaseException as error:
        setattr(error, REQUEST_ID_EXCEPTION_ATTRIBUTE, finalizer.metadata.identifier)
        finalizer.complete(failed=True)
        raise


def _next_synchronous_chunk(content: Generator[bytes]) -> bytes | object:
    """Advance one synchronous stream without exporting ``StopIteration``.

    Returns a private sentinel on exhaustion because asynchronous futures cannot carry
    ``StopIteration`` across their result boundary.

    Arguments:
        content: Correlated synchronous response generator.

    Returns:
        Next response body chunk or the stream-end sentinel.

    Raises:
        BaseException: Any body iteration failure other than normal exhaustion.
    """
    return next(content, STREAM_END)


def _wrap_streaming_content(
    request: HttpRequest,
    response: StreamingHttpResponse,
    *,
    finalizer: _RequestFinalizer,
) -> None:
    """Replace response content with its correlated streaming wrapper.

    Selects the iterator protocol Django recorded on the response while sharing one idempotent
    finalizer between normal, failure, and interruption exits.

    Arguments:
        request: Request whose stream is being returned.
        response: Streaming response whose content should be wrapped.
        finalizer: Terminal metrics and logging operation shared with transport cleanup.

    Returns:
        None.

    Raises:
        None.
    """
    response_state = vars(response)
    registered_closers = cast(
        "list[Callable[[], object]]",
        response_state.get("_resource_closers", []),
    )
    resource_closers = list(registered_closers)
    registered_closers.clear()
    defer_success = isinstance(request, ASGIRequest) and asgi_stream_transport_active.get()
    finalizer.defer_success = defer_success
    finalizer.resource_closers = resource_closers

    if response.is_async:
        asynchronous_content = cast("AsyncIterator[bytes]", response_state["_iterator"])
        source_closer = _AsynchronousSourceCloser(asynchronous_content)
        correlated_asynchronous_content = _correlate_asynchronous_content(
            asynchronous_content,
            finalizer,
            source_closer,
        )
        response.streaming_content = _ClosableAsynchronousContent(
            correlated_asynchronous_content,
            source_closer,
            finalizer,
        )
    else:
        synchronous_content = cast("Iterator[bytes]", response_state["_iterator"])
        correlated_synchronous_content = _correlate_synchronous_content(
            synchronous_content,
            finalizer,
        )

        if isinstance(request, ASGIRequest):
            response.streaming_content = _ClosableSynchronousASGIContent(
                correlated_synchronous_content,
                finalizer,
            )
        else:
            response.streaming_content = _ClosableSynchronousContent(
                correlated_synchronous_content,
                finalizer,
            )


class _ASGIResponseObserver:
    """Observe one HTTP response across receive and send boundaries.

    Owns transport state outside Django so early request rejections, ordinary responses, and
    streams share correlation and terminal accounting.

    Attributes:
        scope: HTTP connection scope.
        receive_callable: Wrapped inbound transport.
        send_callable: Wrapped outbound transport.
        finalizer_holder: Mutable finalizer state shared with Django's request child task.
        identifier: Generated request identifier.
        started: Monotonic request start time.
        response_status: Status observed in the response-start event.
        captured_finalizer: Django finalizer or scope-derived fallback.
        disconnected: Whether the application observed an inbound disconnect.

    Members:
        active_finalizer: Return the finalizer representing this response.
        receive: Track and return the next inbound event.
        send: Correlate and account for one outbound event.
        complete_failure: Finalize an interrupted response.
        close_resources: Close captured response resources on every transport exit.
    """

    def __init__(
        self,
        scope: Scope,
        receive: ASGIReceiveCallable,
        send: ASGISendCallable,
        finalizer_holder: _ASGIFinalizerHolder,
    ) -> None:
        """Capture one HTTP transport.

        Generates correlation before Django constructs its request and retains both transport
        callables for transparent delegation.

        Arguments:
            scope: HTTP connection scope.
            receive: Inbound ASGI event callable.
            send: Outbound ASGI event callable.
            finalizer_holder: Mutable finalizer state shared with Django's request child task.

        Returns:
            None.

        Raises:
            None.
        """
        self.scope = scope
        self.receive_callable = receive
        self.send_callable = send
        self.finalizer_holder = finalizer_holder
        self.identifier = str(uuid.uuid4())
        self.started = time.perf_counter()
        self.response_status: int | None = None
        self.captured_finalizer: _RequestFinalizer | None = None
        self.disconnected = False

    def active_finalizer(self) -> _RequestFinalizer:
        """Return Django's finalizer or build a pre-middleware fallback.

        Uses scope metadata and the observed response status when request construction failed
        before the Django middleware chain could publish its richer finalizer.

        Arguments:
            None.

        Returns:
            Finalizer representing this HTTP response.

        Raises:
            None.
        """
        published_finalizer = self.finalizer_holder.finalizer
        if published_finalizer is not None:
            self.captured_finalizer = published_finalizer

        if self.captured_finalizer is None:
            self.captured_finalizer = _RequestFinalizer(
                _ResponseMetadata(
                    method=cast("str", self.scope.get("method", "")),
                    path=cast("str", self.scope.get("path", "")),
                    status=self.response_status or HTTPStatus.INTERNAL_SERVER_ERROR,
                    view="<unresolved>",
                    started=self.started,
                    identifier=self.identifier,
                )
            )

        return self.captured_finalizer

    async def receive(self) -> ASGIReceiveEvent:
        """Track and return the next inbound ASGI event.

        Records disconnect state without consuming an event itself, allowing Django's listener to
        retain ownership of the receive channel.

        Arguments:
            None.

        Returns:
            Next inbound ASGI event.

        Raises:
            BaseException: Any receive failure from the wrapped transport.
        """
        event = await self.receive_callable()

        if event["type"] == "http.disconnect":
            self.disconnected = True

        return event

    def _prepare_message(self, message: ASGISendEvent) -> ASGISendEvent:
        """Inject correlation into a response-start event.

        Replaces any inner request identifier so the header always matches the outer context that
        owns transport accounting.

        Arguments:
            message: Outbound ASGI event.

        Returns:
            Original body event or correlated response-start event.

        Raises:
            None.
        """
        if message["type"] != "http.response.start":
            return message

        self.response_status = message["status"]
        header_name = REQUEST_ID_HEADER.lower().encode()
        headers = [
            (name, value) for name, value in message["headers"] if name.lower() != header_name
        ]
        headers.append((header_name, self.identifier.encode()))

        return cast("ASGISendEvent", {**message, "headers": headers})

    async def send(self, message: ASGISendEvent) -> None:
        """Send one event and finalize its response when terminal.

        Captures Django's finalizer before delegation, records transport exceptions as failures,
        and accepts success only when the terminal body arrives without an observed disconnect.

        Arguments:
            message: Outbound ASGI event.

        Returns:
            None.

        Raises:
            BaseException: Any transport failure from the wrapped sender.
        """
        outbound_message = self._prepare_message(message)
        self.active_finalizer()

        try:
            await self.send_callable(outbound_message)
        except BaseException:
            self.complete_failure()
            raise

        if message["type"] == "http.response.body" and not message.get("more_body", False):
            self.active_finalizer().complete(failed=self.disconnected)

    def complete_failure(self) -> None:
        """Finalize an interrupted response.

        Uses the idempotent finalizer so overlapping application cancellation and disconnect paths
        cannot duplicate metrics or logs.

        Arguments:
            None.

        Returns:
            None.

        Raises:
            None.
        """
        self.active_finalizer().complete(failed=True)

    async def close_resources(self) -> None:
        """Close captured response resources.

        Runs synchronous Django closers outside the event loop and awaits any asynchronous source
        cleanup registered by the streaming wrapper.

        Arguments:
            None.

        Returns:
            None.

        Raises:
            BaseException: Any asynchronous source cleanup failure.
        """
        finalizer = self.active_finalizer()
        await sync_to_async(finalizer.close_resources, thread_sensitive=True)()

        if finalizer.asynchronous_cleanup is not None:
            await finalizer.asynchronous_cleanup()


def finalize_streaming_asgi(application: ASGI3Application) -> ASGI3Application:
    """Correlate and finalize HTTP requests at the ASGI transport boundary.

    Generates correlation before Django request construction, injects the response identifier, and
    records every response only after terminal body delivery or transport interruption.

    Arguments:
        application: ASGI application whose response events should be observed.

    Returns:
        ASGI application with transport-aware response finalization.

    Raises:
        None.
    """

    async def finalize_response(
        scope: Scope,
        receive: ASGIReceiveCallable,
        send: ASGISendCallable,
    ) -> None:
        """Serve one ASGI scope with transport-aware response completion.

        Preserves non-HTTP behavior and installs one response observer for HTTP correlation,
        disconnect tracking, and terminal accounting.

        Arguments:
            scope: ASGI connection scope.
            receive: Callable yielding inbound ASGI events.
            send: Callable accepting outbound ASGI events.

        Returns:
            None.

        Raises:
            BaseException: Any application or transport failure.
        """
        if scope["type"] != "http":
            await application(scope, receive, send)
            return

        finalizer_holder = _ASGIFinalizerHolder()
        observer = _ASGIResponseObserver(scope, receive, send, finalizer_holder)
        transport_token = asgi_stream_transport_active.set(True)
        finalizer_token = asgi_finalizer_holder.set(finalizer_holder)
        identifier_token = request_identifier.set(observer.identifier)
        started_token = asgi_request_started.set(observer.started)
        try:
            await application(scope, observer.receive, observer.send)
        except BaseException as error:
            setattr(error, REQUEST_ID_EXCEPTION_ATTRIBUTE, observer.identifier)
            observer.complete_failure()
            raise
        finally:
            try:
                observer.complete_failure()
                try:
                    await observer.close_resources()
                except BaseException as error:
                    setattr(error, REQUEST_ID_EXCEPTION_ATTRIBUTE, observer.identifier)
                    raise
            finally:
                asgi_request_started.reset(started_token)
                request_identifier.reset(identifier_token)
                asgi_finalizer_holder.reset(finalizer_token)
                asgi_stream_transport_active.reset(transport_token)

    return finalize_response


@async_only_middleware
def request_context_middleware(
    get_response: Callable[[HttpRequest], Awaitable[HttpResponseBase]],
) -> Callable[[HttpRequest], Awaitable[HttpResponseBase]]:
    """Build middleware that correlates request logs and response headers.

    Creates an asynchronous wrapper that keeps one identifier active for all inner middleware and
    view logs, reports the completed request, and returns the identifier to the caller.

    Arguments:
        get_response: Asynchronous inner middleware chain.

    Returns:
        Asynchronous request middleware.

    Raises:
        None.
    """

    async def correlate(request: HttpRequest) -> HttpResponseBase:
        """Correlate one request and response.

        Generates a fresh UUID rather than trusting a caller-supplied header, keeps it in the
        current asynchronous context until the inner chain finishes, then adds the response header
        and emits a body-free completion record before restoring the previous context.

        Arguments:
            request: Incoming Django request.

        Returns:
            The response with its correlation header.

        Raises:
            None.
        """
        identifier = request_identifier.get()
        if identifier == NO_REQUEST_ID:
            identifier = str(uuid.uuid4())
        started = asgi_request_started.get() or time.perf_counter()
        request.META[REQUEST_ID_META_KEY] = identifier
        token = request_identifier.set(identifier)

        try:
            response = await get_response(request)
            response[REQUEST_ID_HEADER] = identifier
            resolver_match = getattr(request, "resolver_match", None)
            view_name = resolver_match.view_name if resolver_match is not None else "<unresolved>"
            finalizer = _RequestFinalizer(
                _ResponseMetadata(
                    method=request.method or "",
                    path=request.path,
                    status=response.status_code,
                    view=view_name,
                    started=started,
                    identifier=identifier,
                )
            )
            transport_owned = (
                isinstance(request, ASGIRequest) and asgi_stream_transport_active.get()
            )

            if transport_owned:
                holder = cast("_ASGIFinalizerHolder", asgi_finalizer_holder.get())
                holder.finalizer = finalizer

            if isinstance(response, StreamingHttpResponse):
                _wrap_streaming_content(
                    request,
                    response,
                    finalizer=finalizer,
                )
                return response

            if not transport_owned:
                finalizer.complete(failed=False)

            return response
        finally:
            request_identifier.reset(token)

    return correlate


_prometheus_after_middleware = import_module(
    "django_prometheus.middleware"
).PrometheusAfterMiddleware
_prometheus_process_response = cast(
    "Callable[[object, HttpRequest, HttpResponseBase], HttpResponseBase]",
    _prometheus_after_middleware.process_response,
)
_prometheus_process_exception = cast(
    "Callable[[object, HttpRequest, BaseException], None]",
    _prometheus_after_middleware.process_exception,
)


def bounded_prometheus_process_response(
    self: object,
    request: HttpRequest,
    response: HttpResponseBase,
) -> HttpResponseBase:
    """Record django-prometheus response metrics with a bounded method label.

    Preserves the method seen by the rest of Django while collapsing extension methods only for
    the duration of upstream label generation, including its raw-method latency histogram.

    Arguments:
        self: Dynamically derived middleware instance.
        request: Request whose method supplies the metric label.
        response: Final response available at this middleware boundary.

    Returns:
        The unchanged response returned by the upstream middleware.

    Raises:
        None.
    """
    original_method = request.method
    request.method = original_method if original_method in HTTP_METHODS else INVALID_HTTP_METHOD

    try:
        return _prometheus_process_response(self, request, response)
    finally:
        request.method = original_method


def bounded_prometheus_process_exception(
    self: object,
    request: HttpRequest,
    exception: BaseException,
) -> None:
    """Record django-prometheus exception metrics with a bounded method label.

    Normalizes extension methods while the upstream exception hook records latency, then restores
    the original request before Django continues exception handling.

    Arguments:
        self: Dynamically derived middleware instance.
        request: Request whose method supplies the metric label.
        exception: Exception raised while handling the request.

    Returns:
        None.

    Raises:
        None.
    """
    original_method = request.method
    request.method = original_method if original_method in HTTP_METHODS else INVALID_HTTP_METHOD

    try:
        _prometheus_process_exception(self, request, exception)
    finally:
        request.method = original_method


BoundedPrometheusAfterMiddleware = cast(
    "type[object]",
    type(
        "BoundedPrometheusAfterMiddleware",
        (_prometheus_after_middleware,),
        {
            "__doc__": (
                "Bound caller-controlled labels emitted by django-prometheus.\n\n"
                "Inherits from PrometheusAfterMiddleware and normalizes extension methods while "
                "the dependency records response and latency metrics."
            ),
            "process_exception": bounded_prometheus_process_exception,
            "process_response": bounded_prometheus_process_response,
        },
    ),
)


class TaskArgumentRedactionFilter(logging.Filter):
    """Keep task arguments and retry diagnostics out of queue records.

    Inherits from ``logging.Filter`` and blanks task-call arguments plus retry diagnostics that
    Celery emits without exception metadata, preventing either caller or database values from
    bypassing the structured exception reducer.

    Attributes:
        None beyond those the base filter defines.

    Members:
        filter: Blank unsafe fields on one queue record.
    """

    @override
    def filter(self, record: logging.LogRecord) -> bool:
        """Blank unsafe fields on one queue record.

        Leaves the task's name and identifier in place, which is what makes a record traceable,
        while replacing rendered call arguments and diagnostics lacking a separately sanitizable
        exception field.

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

            for field in DATABASE_DIAGNOSTIC_FIELDS:
                if field in data:
                    data[field] = REDACTED_ARGUMENTS

            if record.exc_info is not None:
                exception_type = type(record.exc_info[1]).__name__
                record.msg = "Task %s[%s] failed with %s"
                record.args = (
                    str(data.get("name", "unknown")),
                    str(data.get("id", "unknown")),
                    exception_type,
                )
                record.exc_info = None
                record.exc_text = None

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

        Replaces the interpolated statement with a validated operation name and drops the
        parameters, leaving the alias and duration without retaining any SQL literal.

        Arguments:
            record: The log record to rewrite.

        Returns:
            True, always: the record is kept, only reduced.

        Raises:
            None.
        """
        statement = getattr(record, "sql", None)
        operation = "QUERY"

        if isinstance(statement, str):
            candidate = statement.lstrip().partition(" ")[0].upper()
            operation = candidate if candidate in SQL_OPERATIONS else "QUERY"

        if hasattr(record, "sql"):
            record.msg = f"{operation} database query"
            record.args = ()
            record.db_operation = operation

        for name in QUERY_ATTRIBUTES:
            if hasattr(record, name):
                delattr(record, name)

        return True


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
        exception = record.exc_info[1] if record.exc_info is not None else None
        database_error = _database_exception(exception) if exception is not None else None
        safe_database_exception = (
            _format_database_exception(database_error, record.exc_info[2])
            if database_error is not None and record.exc_info is not None
            else None
        )
        message = (
            _format_database_log_message(record, safe_database_exception)
            if safe_database_exception is not None
            else record.getMessage()
        )
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
                "message": message,
                "module": record.module,
                "line": record.lineno,
                "process": record.process,
                "thread": record.thread,
            }
        )

        if record.exc_info is not None:
            payload["exception"] = (
                safe_database_exception
                if safe_database_exception is not None
                else self.formatException(record.exc_info)
            )

        if record.stack_info is not None:
            payload["stack"] = self.formatStack(record.stack_info)

        if safe_database_exception is not None:
            payload = cast(
                "dict[str, object]",
                _replace_database_diagnostics(payload, safe_database_exception),
            )

        payload = {name: _redact_value(value, field_name=name) for name, value in payload.items()}

        return json.dumps(payload)
