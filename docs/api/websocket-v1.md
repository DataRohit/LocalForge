# LocalForge WebSocket contract

Version: 1

Status: partially runtime-verified

Tickets 38 through 40 runtime-verify the route, host and origin admission, envelope validation, JWT subprotocol
authentication, recoverable unknown-type error, credential rejection codes, malformed-frame close code, clean
connection lifecycle, and user-targeted delivery within and across processes. Limits, throttling, and the complete
failure contract remain planned until Ticket 41 finishes.

## Authentication

The client presents the same short-lived JWT access credential issued by the REST API in the
WebSocket subprotocol header. The query string is never an authentication transport. After successful validation,
the server echoes the accepted subprotocol and places the active account in the connection scope.

The credential is the sole offered subprotocol. An absent list is `credential_absent`; multiple syntactically valid
values, a refresh token, a password-revoked access token, or an otherwise invalid access token are
`credential_malformed`. A syntactically invalid subprotocol header, including a non-ASCII value, is rejected by the
pinned Uvicorn transport with HTTP `400` before ASGI dispatch. Account lookup uses the authoritative primary database
through Channels' database-safe async wrapper. Inactive and unknown or deleted accounts remain distinct close
classifications.

The notification socket is available at `/ws/notifications/`. Tickets 38 and 39 verify the direct ASGI route,
configured Host and Origin admission, subprotocol echo, and a live handshake through Traefik.

## Message envelope

Every text message is a JSON object with exactly these top-level fields:

```json
{
  "type": "notification",
  "payload": {}
}
```

`"type"` is the stable message discriminator. `"payload"` is an object whose fields are defined by that message
type.

## Notification frame

The server delivers an addressed application event in exactly this shape:

```json
{
  "type": "notification",
  "payload": {
    "event": "account.updated",
    "data": {
      "revision": 2
    }
  }
}
```

`payload.event` is the stable application event name clients branch on. `payload.data` is the event-specific JSON
object and is always an object, including when the event carries no fields.

Every accepted socket joins one deterministic group named from only the authenticated account's immutable UUID.
Mutable usernames and email addresses never participate, and no client frame can select, add, or replace group
membership. The server leaves that group on disconnect.

Synchronous views and tasks call
`notifications.delivery.publish_notification(user_id, event, data)`. The helper owns group naming and
channel-layer dispatch. One call delivers the frame once to every socket currently open for that account and to no
socket owned by another account. Publishing when the account has no open socket, including after its last socket
disconnects, is a safe no-op. Delivery through the dedicated channel layer is runtime-verified from a separate
operating-system process.

`user_id` must be an actual `uuid.UUID` instance. Strings, numeric values, bytes, `null`, and objects that merely
expose a `hex` attribute are rejected with `InvalidNotificationRecipientError` before group derivation or
channel-layer acquisition. The helper never parses or coerces recipient identifiers.

The helper validates `event` and `data` against the declared JSON-native domain before channel dispatch. Supported
values inside `data` are strings, finite numbers, booleans, `null`, arrays represented by Python lists, and objects
represented by dictionaries. The `data` root itself must be a dictionary; arrays and scalar values are supported
only beneath that root. Tuples and every other undeclared container or value type raise
`InvalidNotificationDataError`; the helper never silently normalizes them. Every event name, object key, and
string value must encode as UTF-8, so lone surrogate code points raise the same exception. No socket receives an
event after any validation failure.

The outbound consumer also disables the encoder's non-standard `NaN` and infinity spellings, so an internal caller
cannot make it emit a frame a standards-compliant JSON parser rejects.

Every object key at every nesting level must already be a string. The helper rejects integer, float, boolean,
`null`, and other non-string mapping keys with `InvalidNotificationDataError`; it never applies Python's permissive
JSON key coercion and never passes the original non-string key to the channel serializer.

Notification integers use the inclusive range `-9223372036854775808` through `18446744073709551615`. These are the
signed 64-bit minimum and unsigned 64-bit maximum preserved by the configured Channels Redis default MessagePack
serializer. The first integers outside either boundary raise `InvalidNotificationDataError` before channel
dispatch, just like other unsupported data, so callers never receive a transport-specific overflow.

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
| 4402 | `credential_malformed` | The supplied credential is ambiguous, invalid, the wrong token type, or revoked by a password change. | Replace the credential before retrying |
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
for frame limits and connection throttling remain planned. Ticket 39 runtime-verifies `4401` through `4405`, plus
`4500` when authoritative account lookup is unavailable. Authentication and admission rejection complete only the
minimum handshake needed to deliver the private close code; the protected router is never invoked and no application
session is established.
`malformed_frame`, `frame_too_large`, and `server_error` close because continuing cannot safely preserve protocol
state. Ticket 41 may send a final error frame before closing only where the framework can do so reliably, but the
close code remains authoritative.

## Verification state

Ticket 38 establishes and runtime-verifies the route, envelope handling, exact-origin checks, recoverable
unknown-type response, malformed-frame behavior, direct ASGI lifecycle, and live Traefik upgrade. Ticket 39
runtime-verifies Host admission, the subprotocol credential classifications, primary account scope, async-safe
lookup, query-string refusal, and application-log secrecy. Ticket 40 runtime-verifies deterministic server-owned
membership, same-user multi-socket fan-out, user isolation, exact cleanup, safe empty publication, and delivery
from another operating-system process. Ticket 41 will enforce frame size, connection throttling, error frames,
exact close codes, retry semantics, and correlated server-error handling.

The document becomes fully runtime-verified only after all four tickets pass.

## Sources

- [ADR 0018: API error contract](../adr/0018-api-error-contract.md)
- [ADR 0019: WebSocket authentication](../adr/0019-websocket-authentication.md)
- [Ticket 38: ASGI routing and consumer base](../../.scratch/phase-5-websockets/38-asgi-routing-and-consumer-base.md)
- [Ticket 39: WebSocket authentication](../../.scratch/phase-5-websockets/39-websocket-authentication.md)
- [Ticket 40: notification group broadcast](../../.scratch/phase-5-websockets/40-notification-channel-group-broadcast.md)
- [Ticket 41: WebSocket error and close codes](../../.scratch/phase-5-websockets/41-websocket-error-and-close-codes.md)
