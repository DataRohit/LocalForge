"""Unit tests for the WebSocket failure contract.

Locks every stable application error and private close code to one enumeration so middleware,
consumers, documentation, and tests cannot silently establish competing authorities.
"""

from pathlib import Path

import pytest

from notifications.protocol import (
    WebSocketOutcome,
    websocket_close_table_markdown,
    websocket_error_frame,
    websocket_error_table_markdown,
)

pytestmark = pytest.mark.unit

WEBSOCKET_CONTRACT = Path(__file__).resolve().parents[4] / "docs" / "api" / "websocket-v1.md"


def test_websocket_outcomes_are_one_complete_code_authority() -> None:
    """Enumerate every error and close outcome exactly once.

    Compares the public stable-code and close-code pairs with independent contract literals,
    including recoverable outcomes whose connection remains open.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If any outcome is missing, duplicated, or assigned another close code.
    """
    expected = {
        "unknown_message_type": None,
        "malformed_frame": 4400,
        "credential_absent": 4401,
        "credential_malformed": 4402,
        "credential_expired": 4403,
        "account_inactive": 4404,
        "account_not_found": 4405,
        "permission_denied": 4406,
        "frame_too_large": 4407,
        "connection_throttled": 4408,
        "server_error": 4500,
    }

    assert {outcome.code: outcome.close_code for outcome in WebSocketOutcome} == expected
    assert WebSocketOutcome.UNKNOWN_MESSAGE_TYPE.supports_error_frame
    assert WebSocketOutcome.PERMISSION_DENIED.supports_error_frame
    assert all(outcome.retry_guidance for outcome in WebSocketOutcome)


@pytest.mark.parametrize(
    "outcome",
    [
        WebSocketOutcome.UNKNOWN_MESSAGE_TYPE,
        WebSocketOutcome.PERMISSION_DENIED,
    ],
)
def test_recoverable_outcomes_build_the_exact_shared_error_envelope(
    outcome: WebSocketOutcome,
) -> None:
    """Render every recoverable failure through one envelope builder.

    Uses a fixed request identifier and compares the complete frame so consumers cannot diverge
    from the REST-compatible code, message, details, and correlation shape.

    Arguments:
        outcome: Recoverable contract member to render.

    Returns:
        None.

    Raises:
        AssertionError: If the exact error frame differs from the central outcome.
    """
    request_id = "00000000-0000-4000-8000-000000000000"

    assert websocket_error_frame(outcome, request_id) == {
        "type": "error",
        "payload": {
            "code": outcome.code,
            "message": outcome.message,
            "details": outcome.details(),
            "request_id": request_id,
        },
    }


def test_frame_only_outcome_cannot_be_used_as_a_close_code() -> None:
    """Prevent callers from inventing a close code for a recoverable-only failure.

    Exercises the central guard on the unknown-message outcome, which must always keep the socket
    open and return an error frame.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the enum supplies a close code for the frame-only outcome.
    """
    with pytest.raises(ValueError, match="unknown_message_type has no close code"):
        WebSocketOutcome.UNKNOWN_MESSAGE_TYPE.required_close_code()


def test_close_only_outcome_has_empty_details_and_cannot_build_an_error_frame() -> None:
    """Keep close-only outcomes outside recoverable error rendering.

    Exercises the empty-detail representation and the envelope guard with one close-only member,
    covering both central defensive paths.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If a close-only outcome acquires frame details or renders successfully.
    """
    assert WebSocketOutcome.SERVER_ERROR.details() == {}
    with pytest.raises(ValueError, match="server_error does not support an error frame"):
        websocket_error_frame(
            WebSocketOutcome.SERVER_ERROR,
            "00000000-0000-4000-8000-000000000000",
        )


def test_protocol_tables_are_rendered_from_the_outcome_enumeration() -> None:
    """Render error and close documentation from the central authority.

    Verifies every recoverable code and every private close code appears in its generated table,
    keeping the published contract subordinate to the enumeration.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If a table omits or adds an outcome.
    """
    error_table = websocket_error_table_markdown()
    close_table = websocket_close_table_markdown()

    assert {line.split("|")[1].strip(" `") for line in error_table.splitlines()[2:]} == {
        outcome.code for outcome in WebSocketOutcome if outcome.supports_error_frame
    }
    assert {int(line.split("|")[1].strip()) for line in close_table.splitlines()[2:]} == {
        outcome.required_close_code()
        for outcome in WebSocketOutcome
        if outcome.close_code is not None
    }


def test_published_contract_contains_the_generated_outcome_tables() -> None:
    """Bind the checked-in protocol document to the outcome enumeration.

    Reads the client-facing artifact and requires both generated tables verbatim, preventing
    documentation from becoming another authority for error or close values.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the published tables drift from the enumeration.
    """
    document = WEBSOCKET_CONTRACT.read_text(encoding="utf-8")

    assert websocket_error_table_markdown() in document
    assert websocket_close_table_markdown() in document


def test_published_contract_enumerates_transport_owned_outcomes() -> None:
    """Publish every observable pinned-transport failure separately.

    Requires standard transport closes and HTTP handshake rejection to carry client meaning,
    retry guidance, and the explicit absence of application correlation or private codes.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the client contract omits or misclassifies a transport outcome.
    """
    document = WEBSOCKET_CONTRACT.read_text(encoding="utf-8")
    expected_rows = {
        "| 1002 | Malformed WebSocket protocol frame | Fix framing before reconnecting. |",
        "| 1007 | Invalid UTF-8 in a text message | Fix text encoding before reconnecting. |",
        (
            "| 1009 | Message exceeds the Uvicorn transport limit | "
            "Reduce the message before reconnecting. |"
        ),
        (
            "| 1011 | Keepalive ping timeout | Reconnect with bounded backoff and "
            "investigate network health. |"
        ),
        "| 1012 | Server shutdown or restart | Reconnect with bounded backoff. |",
        (
            "| HTTP 400 | Invalid WebSocket handshake syntax | "
            "Fix the handshake before reconnecting. |"
        ),
        (
            "| HTTP 404 | Host does not match the Traefik route | "
            "Use the configured host before reconnecting. |"
        ),
    }
    normalized_document = " ".join(document.split())

    assert expected_rows <= set(document.splitlines())
    assert (
        "Transport-owned outcomes carry no application request ID and no private application "
        "close code because they occur before or outside ASGI handling."
    ) in normalized_document
