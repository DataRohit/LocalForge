# LocalForge WebSocket contract

Version: 1

Status: partially runtime-verified

Ticket 38 runtime-verifies the route, exact-origin admission, envelope validation, recoverable unknown-type error,
malformed-frame close code, and clean connection lifecycle. Authentication, user-targeted delivery, limits,
throttling, and the complete failure contract remain planned until Tickets 39 through 41 finish.

## Authentication

The client presents the same short-lived JWT access credential issued by the REST API in the
WebSocket subprotocol header. The query string is never an authentication transport. After successful validation,
the server echoes the accepted subprotocol and places the active account in the connection scope.

The notification socket is available at `/ws/notifications/`. Ticket 38 verifies the direct ASGI route and a live
handshake through Traefik. Authentication remains planned until Ticket 39.

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

Binary frames, invalid JSON, JSON values that are not objects, envelopes with any missing or extra top-level field,
non-string `"type"` values, and non-object `"payload"` values are malformed frames. No client-originated
application message type is accepted in version 1. A syntactically valid envelope with an unsupported `"type"`
receives an error frame while the connection remains open.

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

The error-frame codes are:

| Code | Meaning | Connection |
| --- | --- | --- |
| `unknown_message_type` | The JSON object names no supported client message type. | Remains open; runtime-verified by Ticket 38 |
| `permission_denied` | The authenticated account may not perform a recoverable operation. | Remains open |

## Close codes

All application close codes use the WebSocket private-use range.

| Close code | Stable code | Meaning | Retry guidance |
| ---: | --- | --- | --- |
| 4400 | `malformed_frame` | A binary frame, invalid JSON, or structurally invalid envelope was received. | Fix the frame before retrying |
| 4401 | `credential_absent` | No JWT subprotocol credential was supplied. | Obtain a credential before retrying |
| 4402 | `credential_malformed` | The supplied credential cannot be decoded or authenticated. | Replace the credential before retrying |
| 4403 | `credential_expired` | The supplied JWT access credential expired. | Refresh authentication, then retry |
| 4404 | `account_inactive` | The credential resolves to an inactive account. | Do not retry until account activation |
| 4405 | `account_not_found` | The credential names an unknown or deleted account. | Stop retrying with this credential |
| 4406 | `permission_denied` | Origin or connection authorization failed. | Retry only after correcting authorization |
| 4407 | `frame_too_large` | The received frame exceeded the configured limit. | Reduce the frame before retrying |
| 4408 | `connection_throttled` | The account exceeded the configured connection rate. | Retry after the server-defined interval |
| 4500 | `server_error` | An unexpected consumer failure was contained and correlated. | Retry with bounded backoff |

Ticket 38 runtime-verifies `4400` for every malformed-frame category above and `4406` for an absent or unlisted
origin. Repeated `Origin` fields are syntactically invalid to the pinned Uvicorn WebSocket transport and are rejected
with HTTP `400` before ASGI dispatch, so no application close frame can exist for that case. The other close codes
remain planned. Authentication rejection closes the handshake because no trusted connection exists for an error
frame. `malformed_frame`, `frame_too_large`, and `server_error` close because continuing cannot safely preserve
protocol state. Ticket 41 may send a final error frame before closing only where the framework can do so reliably,
but the close code remains authoritative.

## Verification state

Ticket 38 establishes and runtime-verifies the route, envelope handling, exact-origin checks, recoverable
unknown-type response, malformed-frame behavior, direct ASGI lifecycle, and live Traefik upgrade. Ticket 39 will
verify the subprotocol credential classifications and prove credentials never enter logs. Ticket 40 will define and
verify notification delivery. Ticket 41 will enforce frame size, connection throttling, error frames, exact close
codes, retry semantics, and correlated server-error handling.

The document becomes fully runtime-verified only after all four tickets pass.

## Sources

- [ADR 0018: API error contract](../adr/0018-api-error-contract.md)
- [ADR 0019: WebSocket authentication](../adr/0019-websocket-authentication.md)
- [Ticket 38: ASGI routing and consumer base](../../.scratch/phase-5-websockets/38-asgi-routing-and-consumer-base.md)
- [Ticket 39: WebSocket authentication](../../.scratch/phase-5-websockets/39-websocket-authentication.md)
- [Ticket 40: notification group broadcast](../../.scratch/phase-5-websockets/40-notification-channel-group-broadcast.md)
- [Ticket 41: WebSocket error and close codes](../../.scratch/phase-5-websockets/41-websocket-error-and-close-codes.md)
