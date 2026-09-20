"""Unit tests for the structured log formatter.

Covers the JSON document each log record becomes, including exception, stack, and caller-supplied
fields, so the collector shipping container output always receives one parsable record per line.
"""

from __future__ import annotations

import json
import logging
import time
from io import StringIO
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn, cast, override
from unittest.mock import Mock

import pytest
from celery.app.task import Context as CeleryContext
from celery.app.trace import RETRY, TraceInfo
from celery.app.trace import logger as celery_trace_logger
from celery.exceptions import Retry
from django.db import DatabaseError
from django.test import RequestFactory

from config.logs import (
    NO_REQUEST_ID,
    REDACTED_ARGUMENTS,
    REPEATED_REFERENCE,
    REQUEST_ID_EXCEPTION_ATTRIBUTE,
    REQUEST_ID_META_KEY,
    QueryRedactionFilter,
    RequestContextFilter,
    StructuredFormatter,
    TaskArgumentRedactionFilter,
    _close_asynchronous_iterator,
    finalize_streaming_asgi,
    request_identifier,
)

if TYPE_CHECKING:
    from asgiref.typing import (
        ASGI3Application,
        ASGIReceiveCallable,
        ASGIReceiveEvent,
        ASGISendCallable,
        ASGISendEvent,
        HTTPResponseBodyEvent,
        HTTPResponseStartEvent,
        Scope,
    )

PROBE_LINE_NUMBER = 42
PROBE_DURATION = 0.004
EXPECTED_REDACTED_HEADERS = 2
ADVERSARIAL_REPEAT_COUNT = 4096
REDACTION_TIME_LIMIT_SECONDS = 1.0
TASK_EXCEPTION_MARKER = "task-exception-marker"
PROBE_HASH = "argon2$argon2id$v=19$m=102400,t=2,p=8$c2FsdA$aGFzaA"
DOCUMENTED_SECRET_FIELDS = (
    "CELERY_BROKER_URL",
    "CELERY_RESULT_BACKEND",
    "DJANGO_SECRET_KEY",
    "FLOWER_BASIC_AUTH",
    "GRAFANA_ADMIN_PASSWORD",
    "PGADMIN_DEFAULT_PASSWORD",
    "POSTGRES_PASSWORD",
    "POSTGRES_REPLICATION_PASSWORD",
    "RABBITMQ_DEFAULT_PASS",
    "S3_ACCESS_KEY_ID",
    "S3_SECRET_ACCESS_KEY",
    "TRAEFIK_DASHBOARD_AUTH",
    "TRAEFIK_DASHBOARD_PASSWORD",
    "VALKEY_CACHE_PASSWORD",
    "VALKEY_CHANNELS_PASSWORD",
)


class Unstringable:
    """Represent a value whose string conversion fails.

    Supplies the failure path the formatter must contain, proving one hostile extra attribute
    cannot discard the whole record.

    Attributes:
        None.

    Members:
        __str__: Refuse string conversion.
    """

    @override
    def __str__(self) -> str:
        """Refuse string conversion.

        Raises deterministically so the formatter's per-field representation fallback is exercised
        without depending on an implementation-specific object.

        Arguments:
            None.

        Returns:
            Never returns.

        Raises:
            ValueError: Always.
        """
        message = "cannot render"
        raise ValueError(message)


class Unrepresentable:
    """Represent a value whose representation fails.

    Supplies the final defensive path in value reduction, proving a hostile object cannot make the
    formatter discard an otherwise valid record.

    Attributes:
        None.

    Members:
        __repr__: Refuse representation.
    """

    @override
    def __repr__(self) -> str:
        """Refuse representation.

        Raises deterministically so the formatter substitutes its fixed safe placeholder instead
        of propagating the object's error.

        Arguments:
            None.

        Returns:
            Never returns.

        Raises:
            ValueError: Always.
        """
        message = "cannot represent"
        raise ValueError(message)


def _raise_value_error(message: str) -> NoReturn:
    """Raise a value error carrying controlled diagnostic text.

    Supplies exception information to formatter tests without embedding a direct raise in the test
    body's validation flow.

    Arguments:
        message: Text the exception should carry.

    Returns:
        Never returns.

    Raises:
        ValueError: Always, carrying the supplied text.
    """
    raise ValueError(message)


def _raise_retry(reason: Retry) -> NoReturn:
    """Raise a Celery retry carrying a controlled database exception.

    Establishes the active exception context Celery's retry handler expects while keeping the
    test's logging assertions separate from exception construction.

    Arguments:
        reason: Retry exception to raise.

    Returns:
        Never returns.

    Raises:
        Retry: Always, carrying the supplied reason.
    """
    raise reason


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
@pytest.mark.asyncio
@pytest.mark.parametrize("send_mode", ["success", "failure"])
async def test_asgi_stream_finalizer_preserves_events_without_a_stream(
    send_mode: str,
) -> None:
    """Preserve non-stream ASGI delivery and transport failures.

    Sends ordinary response events without registering a stream finalizer, proving the wrapper is
    transparent both when delivery succeeds and when the underlying transport raises.

    Arguments:
        send_mode: Whether the wrapped sender accepts or rejects its first event.

    Returns:
        None.

    Raises:
        AssertionError: If events are changed or a transport failure is swallowed.
    """
    events: list[ASGISendEvent] = []

    async def application(
        _scope: Scope,
        _receive: ASGIReceiveCallable,
        send: ASGISendCallable,
    ) -> None:
        """Send one ordinary response.

        Emits a header event followed by the terminal empty body expected for a response with no
        streaming finalizer.

        Arguments:
            _scope: Unused ASGI scope.
            _receive: Unused inbound event callable.
            send: Wrapped outbound event callable.

        Returns:
            None.

        Raises:
            RuntimeError: If the test sender rejects delivery.
        """
        start: HTTPResponseStartEvent = {
            "type": "http.response.start",
            "status": 204,
            "headers": [],
            "trailers": False,
        }
        body: HTTPResponseBodyEvent = {
            "type": "http.response.body",
            "body": b"",
            "more_body": False,
        }
        await send(start)
        await send(body)

    async def receive() -> ASGIReceiveEvent:
        """Return a disconnected inbound state.

        Supplies the callable required by the ASGI contract even though the test application never
        consumes an inbound event.

        Arguments:
            None.

        Returns:
            ASGI disconnect event.

        Raises:
            None.
        """
        return {"type": "http.disconnect"}

    async def send(message: ASGISendEvent) -> None:
        """Collect or reject one outbound event.

        Records successful events verbatim and raises a controlled transport error in failure mode
        so the wrapper's no-finalizer exception branch is exercised.

        Arguments:
            message: Event emitted by the wrapped application.

        Returns:
            None.

        Raises:
            RuntimeError: When failure mode is selected.
        """
        if send_mode == "failure":
            message_text = "transport unavailable"
            raise RuntimeError(message_text)

        events.append(message)

    wrapped = finalize_streaming_asgi(cast("ASGI3Application", application))
    scope = cast("Scope", {"type": "http"})

    if send_mode == "failure":
        with pytest.raises(RuntimeError, match="transport unavailable"):
            await wrapped(scope, receive, send)
    else:
        await wrapped(scope, receive, send)
        assert [event["type"] for event in events] == [
            "http.response.start",
            "http.response.body",
        ]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_asgi_finalizer_preserves_non_http_scopes() -> None:
    """Delegate WebSocket scopes without HTTP observability.

    Sends one WebSocket event through the wrapper, proving HTTP correlation and terminal accounting
    do not alter the channel layer's protocol path.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the wrapper changes or suppresses the WebSocket event.
    """
    events: list[ASGISendEvent] = []

    async def application(
        _scope: Scope,
        _receive: ASGIReceiveCallable,
        send: ASGISendCallable,
    ) -> None:
        """Accept one WebSocket connection.

        Emits a protocol event that the outer wrapper must delegate unchanged while bypassing HTTP
        finalization state.

        Arguments:
            _scope: Unused WebSocket scope.
            _receive: Unused inbound event callable.
            send: Outbound event callable.

        Returns:
            None.

        Raises:
            None.
        """
        await send(
            cast(
                "ASGISendEvent",
                {
                    "type": "websocket.accept",
                    "subprotocol": None,
                    "headers": [],
                },
            )
        )

    async def receive() -> ASGIReceiveEvent:
        """Return a WebSocket disconnect event.

        Supplies the callable required by the ASGI contract without participating in the test
        application's outbound-only behavior.

        Arguments:
            None.

        Returns:
            WebSocket disconnect event.

        Raises:
            None.
        """
        return {"type": "websocket.disconnect", "code": 1000, "reason": ""}

    async def send(message: ASGISendEvent) -> None:
        """Collect one delegated WebSocket event.

        Preserves the event for an exact post-call comparison without adding response headers.
        Leaves the protocol payload unchanged.

        Arguments:
            message: Event emitted by the wrapped application.

        Returns:
            None.

        Raises:
            None.
        """
        events.append(message)

    wrapped = finalize_streaming_asgi(cast("ASGI3Application", application))
    await wrapped(cast("Scope", {"type": "websocket"}), receive, send)

    assert events == [
        {
            "type": "websocket.accept",
            "subprotocol": None,
            "headers": [],
        }
    ]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_asynchronous_close_extension_may_complete_synchronously() -> None:
    """Accept an optional close member that returns no awaitable.

    Exercises defensive capability handling for custom asynchronous iterators whose cleanup
    extension completes immediately.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the optional close member is not invoked.
    """
    iterator = Mock()
    iterator.aclose.return_value = None

    await _close_asynchronous_iterator(iterator)

    iterator.aclose.assert_called_once_with()


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
def test_request_context_is_attached_to_every_record() -> None:
    """Attach the active request identifier to an arbitrary record.

    Confirms the handler filter reads request-local state rather than requiring each logger call to
    supply the identifier, and uses the outside-request sentinel once the context is restored.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If either context value is absent or incorrect.
    """
    filter_ = RequestContextFilter()
    outside = _record()

    assert filter_.filter(outside) is True
    assert outside.__dict__["request_id"] == NO_REQUEST_ID

    token = request_identifier.set("request-1234")
    try:
        inside = _record()
        assert filter_.filter(inside) is True
        assert inside.__dict__["request_id"] == "request-1234"
    finally:
        request_identifier.reset(token)


@pytest.mark.unit
def test_request_context_falls_back_to_the_record_request() -> None:
    """Recover request correlation after the asynchronous context is restored.

    Confirms Django response records carrying the original request receive the identifier stored
    on that request, covering warnings the framework emits outside the inner middleware lifetime.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the request identifier is not attached to the record.
    """
    request = RequestFactory().get("/missing/")
    request.META[REQUEST_ID_META_KEY] = "request-from-record"
    record = _record(request=request)

    assert RequestContextFilter().filter(record) is True
    assert record.__dict__["request_id"] == "request-from-record"


@pytest.mark.unit
def test_request_context_ignores_a_non_text_record_identifier() -> None:
    """Reject a malformed request identifier attached to a framework record.

    Confirms correlation falls back only to the text identifier the middleware stores, rather than
    allowing an arbitrary request metadata value to enter the structured correlation field.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If a non-text request identifier is accepted.
    """
    request = RequestFactory().get("/missing/")
    request.META[REQUEST_ID_META_KEY] = 1234
    record = _record(request=request)

    assert RequestContextFilter().filter(record) is True
    assert record.__dict__["request_id"] == NO_REQUEST_ID


@pytest.mark.unit
def test_request_context_recovers_an_identifier_from_an_escaped_exception() -> None:
    """Correlate a server log emitted after request context restoration.

    Attaches the identifier preserved by streaming middleware to an exception and proves the
    Uvicorn-facing record filter recovers it when no active request or request object remains.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the escaped exception loses its request identifier.
    """
    error = ValueError("stream failed")
    setattr(error, REQUEST_ID_EXCEPTION_ATTRIBUTE, "escaped-request")
    record = _record(exc_info=(type(error), error, error.__traceback__))

    assert RequestContextFilter().filter(record) is True
    assert record.__dict__["request_id"] == "escaped-request"


@pytest.mark.unit
def test_database_exception_rejects_an_invalid_sqlstate() -> None:
    """Exclude malformed database status values from safe diagnostics.

    Supplies a nonstandard driver status alongside a database error, proving the reduced exception
    summary includes only validated five-character SQLSTATE values.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the malformed status enters the structured record.
    """
    error = DatabaseError("database marker")
    vars(error)["sqlstate"] = "invalid-status"
    record = _record(exc_info=(type(error), error, error.__traceback__))
    payload = json.loads(StructuredFormatter().format(record))

    assert "invalid-status" not in payload["exception"]
    assert "database operation failed" in payload["exception"]


@pytest.mark.unit
def test_database_diagnostic_copies_and_malformed_messages_are_reduced() -> None:
    """Reduce copied database exceptions and unsafe message interpolation.

    Supplies an exception object inside cyclic structured extras and an incomplete mapping template,
    proving every fallback produces bounded diagnostics without retaining driver text.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If copied diagnostics, cycles, or malformed formatting bypass reduction.
    """
    marker = "copied-database-diagnostic"
    error = DatabaseError(marker)
    copied: dict[str, object] = {"failure": error}
    copied["nested"] = copied
    record = _record(
        msg="database failure %(missing)s",
        args={"available": "context"},
        exc_info=(type(error), error, error.__traceback__),
        data=copied,
    )
    payload = json.loads(StructuredFormatter().format(record))

    assert marker not in json.dumps(payload)
    assert payload["message"] == "database operation failed"
    assert "database operation failed" in payload["data"]["failure"]
    assert payload["data"]["nested"] == REPEATED_REFERENCE


@pytest.mark.unit
def test_sensitive_fields_and_assignments_are_redacted() -> None:
    """Remove credential-bearing values before a record is serialized.

    Confirms nested fields, text assignments, and URI passwords are all replaced while diagnostic
    fields survive, so structured extras and prose cannot bypass the logging boundary.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If a credential survives or useful context is removed.
    """
    credential = "a-real-credential"
    payload = json.loads(
        StructuredFormatter().format(
            _record(
                msg="password=%s broker=redis://worker:%s@cache:6379/1",
                args=(credential, credential),
                request={
                    "headers": {"Authorization": f"Bearer {credential}"},
                    "password": credential,
                    "path": "/token/login/",
                },
            )
        )
    )
    rendered = json.dumps(payload)

    assert credential not in rendered
    assert payload["request"]["path"] == "/token/login/"
    assert REDACTED_ARGUMENTS in rendered


@pytest.mark.unit
def test_authorization_and_quoted_credentials_are_fully_redacted() -> None:
    """Remove complete credentials containing schemes or whitespace.

    Confirms authorization text and quoted password assignments cannot leave a trailing credential
    after the first word, covering formats the simple login canary does not exercise.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If either complete credential survives.
    """
    authorization = "authorization-canary"
    credential_with_spaces = "horse battery staple"
    message = f"Authorization: Bearer {authorization}"
    payload = json.loads(
        StructuredFormatter().format(
            _record(
                msg=f"{message}; password='{credential_with_spaces}'",
                args=(),
            )
        )
    )

    assert authorization not in payload["message"]
    assert credential_with_spaces not in payload["message"]
    assert REDACTED_ARGUMENTS in payload["message"]


@pytest.mark.unit
@pytest.mark.parametrize("field_name", DOCUMENTED_SECRET_FIELDS)
def test_documented_secret_fields_are_redacted(field_name: str) -> None:
    """Remove every credential field registered by the platform.

    Formats each conventions-registry secret as structured data and textual assignment, proving
    actual environment names cannot bypass generic credential classification.

    Arguments:
        field_name: Registered secret variable under test.

    Returns:
        None.

    Raises:
        AssertionError: If the secret value survives either representation.
    """
    canary = f"{field_name.lower()}-canary"
    payload = json.loads(
        StructuredFormatter().format(
            _record(
                msg=f"{field_name}={canary}",
                args=(),
                settings={field_name: canary},
            )
        )
    )

    assert canary not in json.dumps(payload)


@pytest.mark.unit
@pytest.mark.parametrize("field_name", ["signature", "encryption_key", "client_key"])
def test_generic_key_and_signature_fields_are_redacted(field_name: str) -> None:
    """Remove generic key and signature fields from every log representation.

    Covers structured attributes, nested mappings, ordinary assignments, and multiline signature
    diagnostics so debug logging cannot ship signing material into retained storage.

    Arguments:
        field_name: Generic credential-shaped field under test.

    Returns:
        None.

    Raises:
        AssertionError: If the credential canary survives any representation.
    """
    canary = f"{field_name}-canary"
    payload = json.loads(
        StructuredFormatter().format(
            _record(
                msg=f"{field_name}:\n{canary}",
                args=(),
                **{
                    field_name: canary,
                    "nested": {field_name: canary},
                },
            )
        )
    )

    assert canary not in json.dumps(payload)


@pytest.mark.unit
def test_bracketed_and_block_secret_assignments_are_redacted() -> None:
    """Remove bracketed configuration values and multiline secret blocks.

    Covers Python-style mapping assignment plus YAML literal, folded, and implicit continuation
    forms without consuming the following non-secret field.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If a secret survives or the following safe field is removed.
    """
    canaries = (
        "bracket-secret",
        "literal-line-one",
        "literal-line-two",
        "folded-line-one",
        "folded-line-two",
        "implicit-line-one",
        "implicit-line-two",
    )
    message = (
        f"config['password'] = {canaries[0]}\n"
        f"signature: |\n  {canaries[1]}\n  {canaries[2]}\n"
        f"client_key: >\n  {canaries[3]}\n  {canaries[4]}\n"
        f"encryption_key:\n  {canaries[5]}\n  {canaries[6]}\n"
        "safe_field: retained"
    )
    payload = json.loads(
        StructuredFormatter().format(
            _record(
                msg=message,
                args=(),
            )
        )
    )

    assert all(canary not in payload["message"] for canary in canaries)
    assert "safe_field: retained" in payload["message"]


@pytest.mark.unit
def test_malformed_brackets_and_terminal_block_markers_remain_bounded() -> None:
    """Handle malformed bracket fields and a terminal secret block marker.

    Exercises every bounded parser fallback without treating ordinary malformed field syntax as a
    credential, while still removing a sensitive block marker at end of input.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If safe text is removed or the terminal marker is retained.
    """
    safe_lines = (
        "orphan] = retained-one",
        "config[] = retained-two",
        "config[field] = retained-three",
        "config['field\"] = retained-four",
    )
    payload = json.loads(
        StructuredFormatter().format(
            _record(
                msg="\n".join((*safe_lines, "password: |")),
                args=(),
            )
        )
    )

    assert all(line in payload["message"] for line in safe_lines)
    assert payload["message"].endswith(f"password: {REDACTED_ARGUMENTS}")


@pytest.mark.unit
def test_unquoted_multiword_and_arrow_assignments_are_fully_redacted() -> None:
    """Remove complete unquoted credentials through a structural delimiter.

    Covers whitespace-bearing values and arrow separators so sanitization never preserves a
    credential suffix after replacing only its first token.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If any complete or partial credential survives.
    """
    canaries = ("horse battery staple", "alpha beta", "horse-battery")
    message = f"password: {canaries[0]}; api_token: {canaries[1]}; password => {canaries[2]}"
    payload = json.loads(
        StructuredFormatter().format(
            _record(
                msg=message,
                args=(),
            )
        )
    )

    assert all(canary not in payload["message"] for canary in canaries)
    assert payload["message"].count(REDACTED_ARGUMENTS) == len(canaries)


@pytest.mark.unit
@pytest.mark.parametrize("delimiter", ["&", ";", ",", "}", "]"])
def test_unquoted_credential_punctuation_does_not_expose_a_suffix(
    delimiter: str,
) -> None:
    """Remove complete unquoted credentials containing structural punctuation.

    Distinguishes punctuation inside a credential from a delimiter followed by another named
    assignment, preventing partial disclosure while preserving subsequent safe fields.

    Arguments:
        delimiter: Punctuation character embedded inside the credential.

    Returns:
        None.

    Raises:
        AssertionError: If the credential suffix survives or the safe field is removed.
    """
    canary = f"prefix{delimiter}credential-suffix"
    payload = json.loads(
        StructuredFormatter().format(
            _record(
                msg=f"password={canary}; safe_field=retained",
                args=(),
            )
        )
    )

    assert canary not in payload["message"]
    assert "credential-suffix" not in payload["message"]
    assert "safe_field=retained" in payload["message"]


@pytest.mark.unit
def test_multiline_headers_and_extended_assignments_are_redacted() -> None:
    """Remove complete multiline headers and compound credential field names.

    Confirms header values, escaped quoted values, client secrets, and access tokens cannot leave
    credential suffixes after textual sanitization.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If any credential canary survives.
    """
    canaries = (
        "authorization-canary",
        "sessionid=cookie-canary",
        r"password-prefix\"password-suffix",
        "object-secret-canary",
        "object-token-canary",
    )
    message = (
        f"Authorization: Bearer {canaries[0]} failed\n"
        f"Cookie: csrftoken=csrf-canary; {canaries[1]}\n"
        f'password="{canaries[2]}" '
        f"client_secret='{canaries[3]}' access_token={canaries[4]}"
    )
    payload = json.loads(
        StructuredFormatter().format(
            _record(
                msg=message,
                args=(),
            )
        )
    )

    assert all(canary not in payload["message"] for canary in canaries)
    assert REDACTED_ARGUMENTS in payload["message"]


@pytest.mark.unit
def test_repeated_sensitive_words_are_redacted_in_bounded_time() -> None:
    """Process long non-assignment paths without regex backtracking.

    Formats attacker-controlled paths containing thousands of sensitive-key fragments or spaces
    and enforces a generous linear-time bound that the previous overlapping expressions exceeded.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If formatting becomes pathologically slow.
    """
    path = "/" + ("password-" * ADVERSARIAL_REPEAT_COUNT)
    whitespace_path = "/x" + (" " * (ADVERSARIAL_REPEAT_COUNT * 4))
    uri_path = "/" + ("://a:" * (ADVERSARIAL_REPEAT_COUNT * 2))
    question_path = "/" + ("?" * (ADVERSARIAL_REPEAT_COUNT * 4))
    started = time.perf_counter()

    payload = json.loads(StructuredFormatter().format(_record(http_path=path)))
    whitespace_payload = json.loads(
        StructuredFormatter().format(_record(http_path=whitespace_path))
    )
    uri_payload = json.loads(StructuredFormatter().format(_record(http_path=uri_path)))
    question_payload = json.loads(StructuredFormatter().format(_record(http_path=question_path)))

    assert time.perf_counter() - started < REDACTION_TIME_LIMIT_SECONDS
    assert payload["http_path"] == path
    assert whitespace_payload["http_path"] == whitespace_path
    assert uri_payload["http_path"] == uri_path
    assert question_payload["http_path"] == question_path


@pytest.mark.unit
def test_camel_case_sensitive_fields_and_assignments_are_redacted() -> None:
    """Canonicalize camel-case credential names before classification.

    Confirms structured and textual forms of common session and API key names receive the same
    treatment as their underscore and hyphen variants.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If a camel-case credential survives.
    """
    canary = "camel-case-canary"
    payload = json.loads(
        StructuredFormatter().format(
            _record(
                msg=f"sessionKey={canary}; api.key={canary}; api/key={canary}; api key={canary}",
                args=(),
                sessionid=canary,
                payload={
                    "apiKey": canary,
                    "accessKey": canary,
                    "sessionid": canary,
                },
            )
        )
    )
    rendered = json.dumps(payload)

    assert canary not in rendered
    assert payload["sessionid"] == REDACTED_ARGUMENTS
    assert payload["payload"]["sessionid"] == REDACTED_ARGUMENTS


@pytest.mark.unit
def test_header_pairs_are_redacted_before_serialization() -> None:
    """Remove credentials from textual and byte header pairs.

    Confirms common WSGI and ASGI header representations use the first tuple item as the field name
    when sanitizing the second, rather than treating both items as unrelated sequence values.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If either authorization credential survives.
    """
    text_canary = "text-header-canary"
    byte_canary = "byte-header-canary"
    payload = json.loads(
        StructuredFormatter().format(
            _record(
                headers=[
                    ("Authorization", f"Bearer {text_canary}"),
                    (b"authorization", f"Bearer {byte_canary}".encode()),
                ]
            )
        )
    )
    rendered = json.dumps(payload)

    assert text_canary not in rendered
    assert byte_canary not in rendered
    assert rendered.count(REDACTED_ARGUMENTS) == EXPECTED_REDACTED_HEADERS


@pytest.mark.unit
def test_request_representations_and_repeated_values_are_redacted() -> None:
    """Sanitize values converted to representations after traversal.

    Confirms a Django request URL and a repeated mapping cannot bypass field-aware redaction when
    the formatter reduces arbitrary objects or encounters the same object twice.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If a canary survives either representation path.
    """
    canary = "representation-canary"
    request = RequestFactory().get(f"/missing/%3Ftoken={canary}")
    shared = {"password": canary}
    payload = json.loads(
        StructuredFormatter().format(
            _record(request=request, repeated=[shared, shared]),
        )
    )
    rendered = json.dumps(payload)

    assert canary not in rendered
    assert payload["request"]["method"] == "GET"
    assert canary not in payload["request"]["path"]
    assert REPEATED_REFERENCE in rendered


@pytest.mark.unit
def test_encoded_and_plain_query_tokens_are_redacted_from_exceptions() -> None:
    """Remove sensitive query parameters embedded in exception text.

    Formats an exception carrying encoded and ordinary token parameter names, proving diagnostic
    URLs receive parameter-aware redaction before they are serialized.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If either query token survives.
    """
    encoded_canary = "encoded-exception-canary"
    plain_canary = "plain-exception-canary"
    assignment_marker = "password-exception-canary"

    try:
        message = (
            f"/health?%74oken={encoded_canary} "
            f"/login?next=home&token={plain_canary} password={assignment_marker}"
        )
        _raise_value_error(message)
    except ValueError as error:
        record = _record(exc_info=(type(error), error, error.__traceback__))

    payload = json.loads(StructuredFormatter().format(record))
    rendered = json.dumps(payload)

    assert encoded_canary not in rendered
    assert plain_canary not in rendered
    assert assignment_marker not in rendered


@pytest.mark.unit
def test_uri_userinfo_is_redacted_with_or_without_a_username() -> None:
    """Remove token-only and password-only URI user information.

    Confirms repository tokens and generated Valkey-style password URLs cannot bypass URI
    sanitization merely because their user information omits one side of a username/password pair.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If either URI credential survives.
    """
    access_marker = "uri-token-canary"
    cache_marker = "uri-password-canary"
    payload = json.loads(
        StructuredFormatter().format(
            _record(
                msg=(
                    f"fetch https://{access_marker}@example.invalid/repo "
                    f"cache redis://:{cache_marker}@cache.invalid:6379/1"
                ),
                args=(),
            )
        )
    )

    assert access_marker not in payload["message"]
    assert cache_marker not in payload["message"]


@pytest.mark.unit
def test_quoted_field_names_and_unterminated_values_are_redacted() -> None:
    """Remove assignments with quoted names or unterminated quoted values.

    Exercises diagnostic formats that quote mapping keys and malformed records that reach the
    logging boundary before a credential's closing quote is available.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If either credential marker survives.
    """
    quoted_key_marker = "quoted-key-marker"
    unterminated_marker = "unterminated-marker"
    payload = json.loads(
        StructuredFormatter().format(
            _record(
                msg=(f'"password" = "{quoted_key_marker}" client_secret=\'{unterminated_marker}'),
                args=(),
            )
        )
    )

    assert quoted_key_marker not in payload["message"]
    assert unterminated_marker not in payload["message"]


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
    assert REPEATED_REFERENCE in json.dumps(payload["probe"])
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
def test_a_field_with_broken_string_conversion_uses_its_representation() -> None:
    """Contain a value whose string conversion raises.

    Confirms the document-level fallback reduces only that field to its representation and still
    emits the rest of the structured record.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If formatting raises or the fixed fields disappear.
    """
    payload = json.loads(StructuredFormatter().format(_record(probe=Unstringable())))

    assert payload["level"] == "WARNING"
    assert payload["probe"].startswith("<tests.unit.config.test_logs.Unstringable object")


@pytest.mark.unit
def test_a_field_with_broken_representation_uses_a_safe_placeholder() -> None:
    """Contain a value whose representation raises.

    Confirms even an object that refuses diagnostic representation becomes a fixed harmless value
    while the rest of the record remains available.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If formatting raises or the placeholder is absent.
    """
    payload = json.loads(StructuredFormatter().format(_record(probe=Unrepresentable())))

    assert payload["level"] == "WARNING"
    assert payload["probe"] == "<unrepresentable>"


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
    assert payload["message"] == "INSERT database query"
    assert payload["db_operation"] == "INSERT"
    assert payload["alias"] == "default"
    assert payload["duration"] == PROBE_DURATION


@pytest.mark.unit
def test_query_literals_near_the_operation_are_not_logged() -> None:
    """Discard an interpolated query literal appearing before ordinary SQL structure.

    Confirms a selected credential and an unrecognized leading token are replaced by fixed
    operation metadata rather than copied from Django's already-interpolated statement.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the credential survives or an unknown token becomes the operation.
    """
    canary = "query-canary-secret"
    selected = _record(sql=f"SELECT '{canary}' AS token")
    unknown = _record(sql=f"{canary} SELECT")
    filter_ = QueryRedactionFilter()

    assert filter_.filter(selected) is True
    assert filter_.filter(unknown) is True

    selected_payload = json.loads(StructuredFormatter().format(selected))
    unknown_payload = json.loads(StructuredFormatter().format(unknown))
    rendered = json.dumps([selected_payload, unknown_payload])

    assert canary not in rendered
    assert selected_payload["db_operation"] == "SELECT"
    assert unknown_payload["db_operation"] == "QUERY"


@pytest.mark.unit
def test_failed_query_arguments_are_not_logged_without_rendered_sql() -> None:
    """Discard interpolation arguments when database adaptation fails.

    Confirms a database record carrying ``sql=None`` still receives the fixed query summary and
    loses its arguments before formatting, covering failures that occur before SQL is rendered.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the failed query credential survives.
    """
    canary = "failed-query-canary"
    record = _record(
        msg="(0.000) %s; args=%r; alias=%s",
        args=(None, (canary, object()), "default"),
        sql=None,
        params=(canary, object()),
        alias="default",
    )

    assert QueryRedactionFilter().filter(record) is True

    payload = json.loads(StructuredFormatter().format(record))

    assert canary not in json.dumps(payload)
    assert payload["message"] == "QUERY database query"
    assert payload["db_operation"] == "QUERY"
    assert payload["alias"] == "default"


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


@pytest.mark.unit
def test_task_arguments_are_blanked_on_a_queue_record() -> None:
    """Keep the arguments the queue renders out of its own records.

    Confirms the task's name and identifier survive while the rendered arguments are replaced,
    because those are built from the call rather than from the project's own failure record.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If an argument survives or the identifying context is lost.
    """
    record = _record(
        data={
            "id": "task-1234",
            "name": "tests.probe",
            "args": "('a-real-secret',)",
            "kwargs": "{'api_key': 'another-secret'}",
        }
    )

    assert TaskArgumentRedactionFilter().filter(record) is True
    attached = record.__dict__["data"]

    assert attached["id"] == "task-1234"
    assert attached["name"] == "tests.probe"
    assert attached["args"] == REDACTED_ARGUMENTS
    assert attached["kwargs"] == REDACTED_ARGUMENTS


@pytest.mark.unit
def test_task_failure_exception_text_is_reduced_to_its_type() -> None:
    """Keep arbitrary task exception text and traceback content out of queue logs.

    Supplies a credential-shaped exception through Celery's failure record shape and requires only
    the task identity and exception type to survive structured formatting.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If exception text or traceback content reaches the formatted record.
    """
    exception = RuntimeError(TASK_EXCEPTION_MARKER)
    exception_info = (RuntimeError, exception, None)

    record = _record(
        name="celery.app.trace",
        msg="Task %s[%s] raised unexpected: %r",
        args=("tests.probe", "task-1234", exception_info[1]),
        exc_info=exception_info,
        data={
            "id": "task-1234",
            "name": "tests.probe",
            "exc": repr(exception_info[1]),
            "traceback": TASK_EXCEPTION_MARKER,
        },
    )

    assert TaskArgumentRedactionFilter().filter(record) is True
    payload = json.loads(StructuredFormatter().format(record))

    assert payload["message"] == "Task tests.probe[task-1234] failed with RuntimeError"
    assert payload["data"]["exc"] == REDACTED_ARGUMENTS
    assert payload["data"]["traceback"] == REDACTED_ARGUMENTS
    assert "exception" not in payload
    assert TASK_EXCEPTION_MARKER not in json.dumps(payload)


@pytest.mark.unit
def test_celery_retry_diagnostics_exclude_database_values() -> None:
    """Remove database values from Celery retry records.

    Runs Celery's real retry logging path without ``exc_info``, proving the queue filter rewrites
    both its message argument and copied diagnostic before structured formatting.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the retry record retains the database marker.
    """
    marker = "celery-retry-database-marker"
    reason = Retry(exc=DatabaseError(marker), when=1)
    request = CeleryContext(
        id="retry-observability-probe",
        hostname="localforge-test",
        args=(),
        kwargs={},
    )
    task = Mock()
    task.name = "observability.retry_probe"
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(RequestContextFilter())
    handler.addFilter(TaskArgumentRedactionFilter())
    handler.setFormatter(StructuredFormatter())
    celery_trace_logger.addHandler(handler)

    try:
        _raise_retry(reason)
    except Retry as caught:
        TraceInfo(RETRY, caught).handle_retry(task, request, store_errors=False)
    finally:
        celery_trace_logger.removeHandler(handler)

    payload = json.loads(stream.getvalue())

    assert marker not in json.dumps(payload)
    assert payload["data"]["exc"] == REDACTED_ARGUMENTS


@pytest.mark.unit
def test_a_record_carrying_no_queue_data_is_left_alone() -> None:
    """Pass over a record the queue did not write.

    Confirms a record without the queue's own data attachment is untouched, so the filter cannot
    blank a field that happens to share a name elsewhere in the platform.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the filter rewrites a record it should not.
    """
    record = _record(data="not a mapping")

    assert TaskArgumentRedactionFilter().filter(record) is True
    assert record.__dict__["data"] == "not a mapping"
    assert TaskArgumentRedactionFilter().filter(_record()) is True

    without_arguments = _record(data={"id": "task-1234"})

    assert TaskArgumentRedactionFilter().filter(without_arguments) is True
    assert without_arguments.__dict__["data"] == {"id": "task-1234"}
