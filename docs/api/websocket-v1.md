# LocalForge WebSocket contract

Version: 1

Status: runtime-verified

Tickets 38 through 41 runtime-verify the route, admission policies, envelope validation, authentication,
user-targeted delivery, size limits, throttling, failure containment, and every application error and close outcome.

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
application operation is accepted in version 1. A syntactically valid envelope with an unsupported `"type"`
receives an error frame while the connection remains open. A `"subscribe"` message is recognized only to return
the recoverable `permission_denied` error: membership remains server-owned and the socket stays open.

## Message size limits

The application accepts complete text or binary messages up to exactly 65,536 bytes. Text size is its UTF-8 byte
length. The first byte above that boundary closes with `frame_too_large` before binary classification, JSON
decoding, or message dispatch. A message exactly at the boundary continues through ordinary protocol validation.

Uvicorn independently limits an assembled WebSocket message to 131,072 bytes through
`UVICORN_WEBSOCKET_MAX_SIZE_BYTES`. A message at that boundary reaches ASGI and is therefore closed by the smaller
application limit. The first byte above the transport limit is rejected by Uvicorn with standard WebSocket close
code `1009` before ASGI receives a message, so no application error frame or private close code can exist for that
case.

## Connection admission

After Host, Origin, and JWT authentication but before consumer startup or group membership, the server records one
connection attempt for the authenticated immutable user ID. The default
`DJANGO_WEBSOCKET_CONNECTION_THROTTLE_RATE` is `30/minute`.

The policy is an epoch-aligned fixed window using time from the dedicated Channels Valkey instance. At most thirty
authenticated attempts are admitted in each sixty-second window; rejected attempts do not increment the count.
Disconnecting does not refund an attempt. The key expires just after the active window boundary, and the first
attempt in the next window resets the stored window and count. The same atomic script and key are shared by every
Django worker and process.

Admission has the separate `DJANGO_WEBSOCKET_CONNECTION_ADMISSION_TIMEOUT_SECONDS` bound. Limit exhaustion closes
with `connection_throttled`. Timeout or channel-store failure is logged with the connection request identifier and
fails closed with `server_error`; the server never silently admits without shared state.

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

The following table is rendered from `notifications.protocol.WebSocketOutcome`, the application authority:

| Code | Message | Connection |
| --- | --- | --- |
| `unknown_message_type` | The message type is not supported. | Remains open |
| `permission_denied` | The operation is not permitted. | Remains open |

A prohibited client-selected subscription returns:

```json
{
  "type": "error",
  "payload": {
    "code": "permission_denied",
    "message": "The operation is not permitted.",
    "details": {
      "type": ["The authenticated account cannot perform this operation."]
    },
    "request_id": "00000000-0000-4000-8000-000000000000"
  }
}
```

## Close codes

All application close codes use the WebSocket private-use range. The table is rendered from
`notifications.protocol.WebSocketOutcome`, including its retry guidance:

| Close code | Stable code | Meaning | Retry guidance |
| ---: | --- | --- | --- |
| 4400 | `malformed_frame` | The frame is malformed. | Fix the frame before reconnecting. |
| 4401 | `credential_absent` | A credential is required. | Obtain an access credential before reconnecting. |
| 4402 | `credential_malformed` | The credential is invalid. | Replace the credential before reconnecting. |
| 4403 | `credential_expired` | The credential has expired. | Refresh authentication before reconnecting. |
| 4404 | `account_inactive` | The account is inactive. | Reconnect only after the account is activated. |
| 4405 | `account_not_found` | The account does not exist. | Do not reconnect with this credential. |
| 4406 | `permission_denied` | The operation is not permitted. | Correct authorization before retrying. |
| 4407 | `frame_too_large` | The frame is too large. | Reduce the frame before reconnecting. |
| 4408 | `connection_throttled` | The connection rate limit was exceeded. | Retry after the active fixed window expires. |
| 4500 | `server_error` | The server could not continue the connection. | Reconnect with bounded backoff. |

### Transport-owned outcomes

The pinned Uvicorn `websockets-sansio` transport can reject a connection or close an established socket without an
application error frame. These outcomes remain separate from `WebSocketOutcome`, which is the authority only for
LocalForge application codes.

| Outcome | Meaning | Retry guidance |
| ---: | --- | --- |
| 1002 | Malformed WebSocket protocol frame | Fix framing before reconnecting. |
| 1007 | Invalid UTF-8 in a text message | Fix text encoding before reconnecting. |
| 1009 | Message exceeds the Uvicorn transport limit | Reduce the message before reconnecting. |
| 1011 | Keepalive ping timeout | Reconnect with bounded backoff and investigate network health. |
| 1012 | Server shutdown or restart | Reconnect with bounded backoff. |
| HTTP 400 | Invalid WebSocket handshake syntax | Fix the handshake before reconnecting. |
| HTTP 404 | Host does not match the Traefik route | Use the configured host before reconnecting. |

Code `1002` includes an unmasked client frame or other framing violation. Code `1007` is emitted while the transport
decodes a text message, before ASGI receives it. Code `1009` applies above the configured 131,072-byte transport
limit. Code `1011` is emitted when the client does not answer Uvicorn's keepalive ping within its configured timeout;
the deployed interval and timeout retain Uvicorn's pinned 20-second defaults. Code `1012` is emitted to established
sockets during graceful server shutdown or restart.

HTTP `400` includes repeated `Origin` fields and syntactically invalid subprotocol headers rejected during the
handshake. A Host mismatch at the public Traefik entry point cannot select the application router and returns HTTP
`404`; a Host mismatch sent directly to Uvicorn reaches application admission and closes `4406`. Transport-owned
outcomes carry no application request ID and no private application close code because they occur before or outside
ASGI handling.

Authentication, admission, and authorization rejection complete only enough handshake to deliver the private
close code; the protected consumer does not start. `malformed_frame`, `frame_too_large`, and `server_error` close
because continuing cannot safely preserve protocol state.

An unexpected consumer exception is contained by the outer WebSocket failure boundary. The boundary emits only
`server_error`, logs a fixed message carrying the same UUID request identifier used by error frames, and records no
exception traceback, credential, JWT, payload, or secret. Consumer group cleanup runs before the boundary closes,
including exception and cancellation paths. The consumer claims cleanup ownership before awaiting group addition,
so cancellation after the channel layer records local or remote membership still performs one idempotent discard.
That discard is shielded until the channel layer processes Redis' unsubscribe acknowledgement; only then is cleanup
ownership released and the original cancellation propagated.

## Verification state

Ticket 38 establishes the route, envelope, origin checks, malformed-frame handling, and live upgrade. Ticket 39
establishes Host admission, JWT subprotocol classifications, authoritative account scope, query-string refusal,
and credential secrecy. Ticket 40 establishes deterministic server-owned membership, fan-out, isolation, cleanup,
strict notification publication, and cross-process delivery. Ticket 41 establishes the central outcome
enumeration, recoverable permission response, both message limits, shared connection admission, store-failure
handling, correlated exception containment, and exact cleanup on disconnect, cancellation, and failure.

All four WebSocket tickets are runtime-verified.

## Sources

- [ADR 0018: API error contract](../adr/0018-api-error-contract.md)
- [ADR 0019: WebSocket authentication](../adr/0019-websocket-authentication.md)
- [Ticket 38: ASGI routing and consumer base](../../.scratch/phase-5-websockets/38-asgi-routing-and-consumer-base.md)
- [Ticket 39: WebSocket authentication](../../.scratch/phase-5-websockets/39-websocket-authentication.md)
- [Ticket 40: notification group broadcast](../../.scratch/phase-5-websockets/40-notification-channel-group-broadcast.md)
- [Ticket 41: WebSocket error and close codes](../../.scratch/phase-5-websockets/41-websocket-error-and-close-codes.md)
