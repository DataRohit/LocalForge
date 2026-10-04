"""Authoritative WebSocket error and close-code contract.

Defines every application failure once, including its stable code, optional private close code,
recoverable frame capability, client message, detail, and retry guidance.
"""

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, TypedDict, cast

if TYPE_CHECKING:
    from asgiref.typing import ASGIReceiveCallable, ASGISendCallable, ASGISendEvent


class WebSocketErrorPayload(TypedDict):
    """Describe the REST-compatible payload inside one WebSocket error frame.

    Inherits from ``TypedDict`` and fixes the code, message, detail, and correlation fields every
    recoverable failure sends.

    Attributes:
        code: Stable machine-readable outcome code.
        message: Human-readable outcome message.
        details: Per-field validation-style detail.
        request_id: Connection correlation identifier.

    Members:
        None.
    """

    code: str
    message: str
    details: dict[str, list[str]]
    request_id: str


class WebSocketErrorFrame(TypedDict):
    """Describe one recoverable WebSocket error frame.

    Inherits from ``TypedDict`` and preserves the shared top-level type and payload envelope used
    by every open-connection failure.

    Attributes:
        type: Stable frame discriminator.
        payload: REST-compatible error payload.

    Members:
        None.
    """

    type: str
    payload: WebSocketErrorPayload


@dataclass(frozen=True, slots=True)
class WebSocketOutcomeContract:
    """Store one immutable WebSocket failure contract row.

    Holds every client-visible value as one enum value object, avoiding positional enum
    construction and making additions explicit at the central authority.

    Attributes:
        code: Stable machine-readable outcome code.
        close_code: Private close code, or ``None`` for frame-only failures.
        supports_error_frame: Whether the connection may remain open.
        client_message: Human-readable error-frame message.
        detail_field: Optional error detail field.
        detail_message: Optional error detail text.
        retry_guidance: Client retry instruction.

    Members:
        None.
    """

    code: str
    close_code: int | None
    supports_error_frame: bool
    client_message: str
    detail_field: str | None
    detail_message: str | None
    retry_guidance: str


class WebSocketOutcome(Enum):
    """Describe one client-visible WebSocket failure outcome.

    Inherits from ``Enum`` and centralizes the values consumed by admission middleware, consumer
    error handling, documentation rendering, and public-seam tests.

    Attributes:
        code: Stable machine-readable error code.
        close_code: Private close code, or ``None`` for frame-only failures.
        supports_error_frame: Whether the outcome can be sent while the connection remains open.
        message: Human-readable client message used by recoverable frames.
        detail_field: Error detail field used by recoverable frames.
        detail_message: Error detail text used by recoverable frames.
        retry_guidance: Stable client retry instruction published in the contract.

    Members:
        details: Build the REST-compatible detail object for an error frame.
        required_close_code: Return the private close code for an unrecoverable outcome.
    """

    UNKNOWN_MESSAGE_TYPE = WebSocketOutcomeContract(
        code="unknown_message_type",
        close_code=None,
        supports_error_frame=True,
        client_message="The message type is not supported.",
        detail_field="type",
        detail_message="No handler is registered for this message type.",
        retry_guidance="Correct the message type before sending another frame.",
    )
    MALFORMED_FRAME = WebSocketOutcomeContract(
        code="malformed_frame",
        close_code=4400,
        supports_error_frame=False,
        client_message="The frame is malformed.",
        detail_field=None,
        detail_message=None,
        retry_guidance="Fix the frame before reconnecting.",
    )
    CREDENTIAL_ABSENT = WebSocketOutcomeContract(
        code="credential_absent",
        close_code=4401,
        supports_error_frame=False,
        client_message="A credential is required.",
        detail_field=None,
        detail_message=None,
        retry_guidance="Obtain an access credential before reconnecting.",
    )
    CREDENTIAL_MALFORMED = WebSocketOutcomeContract(
        code="credential_malformed",
        close_code=4402,
        supports_error_frame=False,
        client_message="The credential is invalid.",
        detail_field=None,
        detail_message=None,
        retry_guidance="Replace the credential before reconnecting.",
    )
    CREDENTIAL_EXPIRED = WebSocketOutcomeContract(
        code="credential_expired",
        close_code=4403,
        supports_error_frame=False,
        client_message="The credential has expired.",
        detail_field=None,
        detail_message=None,
        retry_guidance="Refresh authentication before reconnecting.",
    )
    ACCOUNT_INACTIVE = WebSocketOutcomeContract(
        code="account_inactive",
        close_code=4404,
        supports_error_frame=False,
        client_message="The account is inactive.",
        detail_field=None,
        detail_message=None,
        retry_guidance="Reconnect only after the account is activated.",
    )
    ACCOUNT_NOT_FOUND = WebSocketOutcomeContract(
        code="account_not_found",
        close_code=4405,
        supports_error_frame=False,
        client_message="The account does not exist.",
        detail_field=None,
        detail_message=None,
        retry_guidance="Do not reconnect with this credential.",
    )
    PERMISSION_DENIED = WebSocketOutcomeContract(
        code="permission_denied",
        close_code=4406,
        supports_error_frame=True,
        client_message="The operation is not permitted.",
        detail_field="type",
        detail_message="The authenticated account cannot perform this operation.",
        retry_guidance="Correct authorization before retrying.",
    )
    FRAME_TOO_LARGE = WebSocketOutcomeContract(
        code="frame_too_large",
        close_code=4407,
        supports_error_frame=False,
        client_message="The frame is too large.",
        detail_field=None,
        detail_message=None,
        retry_guidance="Reduce the frame before reconnecting.",
    )
    CONNECTION_THROTTLED = WebSocketOutcomeContract(
        code="connection_throttled",
        close_code=4408,
        supports_error_frame=False,
        client_message="The connection rate limit was exceeded.",
        detail_field=None,
        detail_message=None,
        retry_guidance="Retry after the active fixed window expires.",
    )
    SERVER_ERROR = WebSocketOutcomeContract(
        code="server_error",
        close_code=4500,
        supports_error_frame=False,
        client_message="The server could not continue the connection.",
        detail_field=None,
        detail_message=None,
        retry_guidance="Reconnect with bounded backoff.",
    )

    @property
    def code(self) -> str:
        """Return the stable machine-readable code.

        Exposes the value clients branch on without revealing the enum's storage
        representation.

        Arguments:
            None.

        Returns:
            Stable error code.
        """
        return self.value.code

    @property
    def close_code(self) -> int | None:
        """Return the assigned private close code.

        Distinguishes close-capable outcomes from failures that must remain
        recoverable frames.

        Arguments:
            None.

        Returns:
            Private close code, or ``None`` for frame-only failures.
        """
        return self.value.close_code

    @property
    def supports_error_frame(self) -> bool:
        """Report whether this outcome can keep the connection open.

        Lets the envelope builder reject close-only failures instead of silently
        rendering them.

        Arguments:
            None.

        Returns:
            Whether an error frame is permitted.
        """
        return self.value.supports_error_frame

    @property
    def message(self) -> str:
        """Return the human-readable error-frame message.

        Exposes only the fixed client-safe text stored by the central
        contract.

        Arguments:
            None.

        Returns:
            Client-safe error message.
        """
        return self.value.client_message

    @property
    def retry_guidance(self) -> str:
        """Return the published client retry instruction.

        Keeps documentation rendering tied to the same outcome clients receive
        at runtime.

        Arguments:
            None.

        Returns:
            Stable retry guidance.
        """
        return self.value.retry_guidance

    def details(self) -> dict[str, list[str]]:
        """Build the per-field detail object for a recoverable error.

        Returns an empty object when the outcome needs no field-specific explanation, preserving
        the same envelope shape used by REST errors.

        Arguments:
            None.

        Returns:
            Error details keyed by the affected field.

        Raises:
            None.
        """
        if self.value.detail_field is None or self.value.detail_message is None:
            return {}

        return {self.value.detail_field: [self.value.detail_message]}

    def required_close_code(self) -> int:
        """Return the private close code assigned to this outcome.

        Rejects frame-only outcomes so a caller cannot accidentally close with an invented value
        when the contract requires an open connection.

        Arguments:
            None.

        Returns:
            Private-use WebSocket close code.

        Raises:
            ValueError: If this outcome has no close code.
        """
        if self.close_code is None:
            message = f"{self.code} has no close code"
            raise ValueError(message)

        return self.close_code


def websocket_error_frame(outcome: WebSocketOutcome, request_id: str) -> WebSocketErrorFrame:
    """Build one recoverable failure frame from the central contract.

    Refuses close-only outcomes so every error frame is both explicitly permitted and rendered
    from the same code, message, details, and request identifier shape.

    Arguments:
        outcome: Recoverable failure contract member.
        request_id: Connection correlation identifier.

    Returns:
        Exact error frame ready for JSON serialization.

    Raises:
        ValueError: If the outcome cannot be sent while the connection remains open.
    """
    if not outcome.supports_error_frame:
        message = f"{outcome.code} does not support an error frame"
        raise ValueError(message)

    return {
        "type": "error",
        "payload": {
            "code": outcome.code,
            "message": outcome.message,
            "details": outcome.details(),
            "request_id": request_id,
        },
    }


async def close_rejected_handshake(
    receive: ASGIReceiveCallable,
    send: ASGISendCallable,
    outcome: WebSocketOutcome,
) -> None:
    """Complete only enough handshake to deliver one private application close code.

    Waits for the connection event, accepts no protected application behavior, and closes
    immediately so real clients can observe the central rejection outcome.

    Arguments:
        receive: Callable yielding the initial connection event.
        send: Callable emitting handshake and close events.
        outcome: Unrecoverable contract member assigned to the rejection.

    Returns:
        None.

    Raises:
        ValueError: If the outcome has no application close code.
    """
    await receive()
    await send(cast("ASGISendEvent", {"type": "websocket.accept"}))
    await send(
        cast(
            "ASGISendEvent",
            {
                "type": "websocket.close",
                "code": outcome.required_close_code(),
            },
        )
    )


def websocket_error_table_markdown() -> str:
    """Render the recoverable error-frame contract as Markdown.

    Iterates the central enumeration so documentation cannot establish another list of codes,
    messages, or connection behavior.

    Arguments:
        None.

    Returns:
        Markdown table for every error-frame-capable outcome.
    """
    rows = [
        "| Code | Message | Connection |",
        "| --- | --- | --- |",
    ]
    rows.extend(
        f"| `{outcome.code}` | {outcome.message} | Remains open |"
        for outcome in WebSocketOutcome
        if outcome.supports_error_frame
    )

    return "\n".join(rows)


def websocket_close_table_markdown() -> str:
    """Render the private close-code contract as Markdown.

    Iterates the central enumeration so close values and retry guidance in the published contract
    remain mechanically tied to the application authority.

    Arguments:
        None.

    Returns:
        Markdown table for every outcome carrying a close code.
    """
    rows = [
        "| Close code | Stable code | Meaning | Retry guidance |",
        "| ---: | --- | --- | --- |",
    ]
    rows.extend(
        (
            f"| {outcome.required_close_code()} | `{outcome.code}` | "
            f"{outcome.message} | {outcome.retry_guidance} |"
        )
        for outcome in WebSocketOutcome
        if outcome.close_code is not None
    )

    return "\n".join(rows)
