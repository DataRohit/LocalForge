# LocalForge WebSocket contract

Version: 1

Status: planned

This contract is not runtime-verified. Tickets 38 through 41 own its Phase 5 implementation and executable tests.
No WebSocket route exists yet, and this document does not add one or claim that the handshake, messages, or close
codes are currently available.

## Authentication

The client presents the same short-lived JWT access credential issued by the REST API in the
WebSocket subprotocol header. The query string is never an authentication transport. After successful validation,
the server echoes the accepted subprotocol and places the active account in the connection scope.

The exact route is deliberately unspecified until Ticket 38 adds it to the authoritative Channels router and proxy
configuration. This avoids documenting an invented path before runtime behavior exists.

## Message envelope

Every text message is a JSON object with exactly these top-level fields:

```json
{
  "type": "notification",
  "payload": {}
}
```

`"type"` is the stable message discriminator. `"payload"` is an object whose fields are defined by that message
type. Ticket 40 owns the first notification payload and its cross-process delivery tests.

Binary frames and JSON values that are not objects are malformed frames. No client-originated application message
type is accepted in the planned version. A syntactically valid object with an unsupported `"type"` receives an
error frame while the connection remains open.

## Error frame

Recoverable application errors use the REST error envelope inside the message payload:

```json
{
  "type": "error",
  "payload": {
    "code": "unknown_message_type",
    "message": "The message type is not supported.",
    "details": {
      "type": ["No handler is registered for this message type."]
    },
    "request_id": "00000000-0000-4000-8000-000000000000"
  }
}
```

The planned error-frame codes are:

| Code | Meaning | Connection |
| --- | --- | --- |
| `unknown_message_type` | The JSON object names no supported client message type. | Remains open |
| `permission_denied` | The authenticated account may not perform a recoverable operation. | Remains open |

## Close codes

All application close codes use the WebSocket private-use range.

| Close code | Stable code | Meaning | Retry guidance |
| ---: | --- | --- | --- |
| 4400 | `malformed_frame` | A binary frame, invalid JSON, or non-object JSON value was received. | Fix the frame before retrying |
| 4401 | `credential_absent` | No JWT subprotocol credential was supplied. | Obtain a credential before retrying |
| 4402 | `credential_malformed` | The supplied credential cannot be decoded or authenticated. | Replace the credential before retrying |
| 4403 | `credential_expired` | The supplied JWT access credential expired. | Refresh authentication, then retry |
| 4404 | `account_inactive` | The credential resolves to an inactive account. | Do not retry until account activation |
| 4405 | `account_not_found` | The credential names an unknown or deleted account. | Stop retrying with this credential |
| 4406 | `permission_denied` | Origin or connection authorization failed. | Retry only after correcting authorization |
| 4407 | `frame_too_large` | The received frame exceeded the configured limit. | Reduce the frame before retrying |
| 4408 | `connection_throttled` | The account exceeded the configured connection rate. | Retry after the server-defined interval |
| 4500 | `server_error` | An unexpected consumer failure was contained and correlated. | Retry with bounded backoff |

Authentication rejection closes the handshake because no trusted connection exists for an error frame.
`malformed_frame`, `frame_too_large`, and `server_error` close because continuing cannot safely preserve protocol
state. Ticket 41 may send a final error frame before closing only where the framework can do so reliably, but the
close code remains authoritative.

## Verification state

Ticket 38 will establish the route, envelope handling, origin checks, and malformed-frame behavior. Ticket 39 will
verify the subprotocol credential classifications and prove credentials never enter logs. Ticket 40 will define and
verify notification delivery. Ticket 41 will enforce frame size, connection throttling, error frames, exact close
codes, retry semantics, and correlated server-error handling.

Until all four tickets pass, every item in this document remains planned and not runtime-verified.

## Sources

- [ADR 0018: API error contract](../adr/0018-api-error-contract.md)
- [ADR 0019: WebSocket authentication](../adr/0019-websocket-authentication.md)
- [Ticket 38: ASGI routing and consumer base](../../.scratch/phase-5-websockets/38-asgi-routing-and-consumer-base.md)
- [Ticket 39: WebSocket authentication](../../.scratch/phase-5-websockets/39-websocket-authentication.md)
- [Ticket 40: notification group broadcast](../../.scratch/phase-5-websockets/40-notification-channel-group-broadcast.md)
- [Ticket 41: WebSocket error and close codes](../../.scratch/phase-5-websockets/41-websocket-error-and-close-codes.md)
