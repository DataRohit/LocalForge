"""Shared WebSocket connection admission.

Uses the dedicated Channels Valkey instance and server time to enforce one authenticated-user
fixed-window budget across every Django worker without blocking the event loop.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from django.conf import settings
from redis.asyncio import Redis
from redis.exceptions import RedisError

from accounts.request_throttling import parse_throttle_rate
from config.logs import REQUEST_ID_META_KEY
from notifications.protocol import WebSocketOutcome, close_rejected_handshake

if TYPE_CHECKING:
    from uuid import UUID

    from asgiref.typing import ASGI3Application, ASGIReceiveCallable, ASGISendCallable, Scope

    from accounts.models import User

logger = logging.getLogger(__name__)

TEST_SERVER_TIME_MILLISECONDS: int | None = None
CONNECTION_ADMISSION_SCRIPT = """
local limit = tonumber(ARGV[1])
local window_ms = tonumber(ARGV[2])
local now_ms
if ARGV[3] ~= "" then
    now_ms = tonumber(ARGV[3])
else
    local server_time = redis.call("TIME")
    now_ms = server_time[1] * 1000 + math.floor(server_time[2] / 1000)
end
local window = math.floor(now_ms / window_ms)
local retry_ms = window_ms - (now_ms % window_ms)
local stored = redis.call("GET", KEYS[1])
local count = 0
if stored then
    local separator = string.find(stored, ":")
    if separator then
        local stored_window = tonumber(string.sub(stored, 1, separator - 1))
        if stored_window == window then
            count = tonumber(string.sub(stored, separator + 1)) or 0
        end
    end
end
if count >= limit then
    return {0, math.max(1, math.ceil(retry_ms / 1000))}
end
redis.call("SET", KEYS[1], window .. ":" .. (count + 1), "PX", retry_ms + 1000)
return {1, 0}
"""


@dataclass(frozen=True, slots=True)
class WebSocketAdmissionDecision:
    """Represent one shared connection admission outcome.

    Stores whether the authenticated handshake may continue and the whole-second delay until the
    current fixed window ends when its account budget is exhausted.

    Attributes:
        admitted: Whether the connection attempt was recorded and admitted.
        retry_after_seconds: Delay before the next fixed window can admit.

    Members:
        None.
    """

    admitted: bool
    retry_after_seconds: int


class WebSocketAdmissionStoreError(RuntimeError):
    """Signal that shared connection admission could not reach authoritative state.

    Inherits from ``RuntimeError`` and carries no host, password, or driver message, allowing
    middleware to log a stable correlated failure without exposing store credentials.

    Attributes:
        None.

    Members:
        None.
    """


class WebSocketConnectionAdmissionStore:
    """Enforce one fixed-window account budget on the channel Valkey instance.

    Inherits nothing and executes one atomic server-timed script, making admission shared across
    workers while automatic key expiry cleans state shortly after each epoch-aligned window.

    Attributes:
        host: Channel Valkey host.
        port: Channel Valkey port.
        password: Channel Valkey credential.
        prefix: Application namespace shared with the channel layer.
        socket_timeout_seconds: Connection and operation timeout.

    Members:
        admit: Atomically record or reject one account connection attempt.
        from_settings: Build the store from the configured channel layer.
    """

    def __init__(
        self,
        *,
        host: str,
        port: int,
        password: str,
        prefix: str,
        socket_timeout_seconds: float,
    ) -> None:
        """Store one dedicated Valkey endpoint configuration.

        Keeps all connection material private to the adapter so callers handle only UUIDs and
        admission outcomes.

        Arguments:
            host: Channel Valkey host.
            port: Channel Valkey port.
            password: Channel Valkey credential.
            prefix: Application namespace.
            socket_timeout_seconds: Connection and operation timeout.

        Returns:
            None.
        """
        self.host = host
        self.port = port
        self.password = password
        self.prefix = prefix
        self.socket_timeout_seconds = socket_timeout_seconds

    @classmethod
    def from_settings(cls) -> WebSocketConnectionAdmissionStore:
        """Build the store from the configured channel layer host list.

        Reuses the exact dedicated endpoint, credential, namespace, and socket bound already
        governing cross-worker WebSocket delivery.

        Arguments:
            None.

        Returns:
            Store configured for the active environment.

        Raises:
            KeyError: If the channel-layer setting is structurally incomplete.
        """
        layer = cast("dict[str, Any]", settings.CHANNEL_LAYERS["default"])
        configuration = cast("dict[str, Any]", layer["CONFIG"])
        host = cast("list[dict[str, Any]]", configuration["hosts"])[0]

        return cls(
            host=cast("str", host["host"]),
            port=cast("int", host["port"]),
            password=cast("str", host["password"]),
            prefix=cast("str", configuration["prefix"]),
            socket_timeout_seconds=float(host["socket_timeout"]),
        )

    async def admit(
        self,
        user_id: UUID,
        *,
        limit: int,
        window_seconds: int,
        now_milliseconds: int | None = None,
    ) -> WebSocketAdmissionDecision:
        """Atomically admit one authenticated account connection attempt.

        Uses Valkey server time in production, counts admitted attempts in an epoch-aligned fixed
        window, rejects without incrementing once full, and expires state just after the boundary.

        Arguments:
            user_id: Immutable authenticated account identifier.
            limit: Maximum admitted attempts in one window.
            window_seconds: Positive fixed-window duration.
            now_milliseconds: Coordinated deterministic test time, or ``None`` for server time.

        Returns:
            Shared admission outcome and retry delay.

        Raises:
            ValueError: If the limit or duration is not positive.
            WebSocketAdmissionStoreError: If Valkey cannot make an authoritative decision.
        """
        if limit <= 0 or window_seconds <= 0:
            message = "connection admission limit and window must be positive"
            raise ValueError(message)

        key = f"{self.prefix}:websocket-admission:{user_id.hex}"
        try:
            async with Redis(
                host=self.host,
                port=self.port,
                password=self.password,
                socket_connect_timeout=self.socket_timeout_seconds,
                socket_timeout=self.socket_timeout_seconds,
            ) as client:
                raw = await client.eval(
                    CONNECTION_ADMISSION_SCRIPT,
                    1,
                    key,
                    limit,
                    window_seconds * 1000,
                    "" if now_milliseconds is None else now_milliseconds,
                )
        except (OSError, RedisError) as error:
            raise WebSocketAdmissionStoreError from error

        values = list(cast("list[object]", raw))

        return WebSocketAdmissionDecision(
            admitted=bool(int(cast("int", values[0]))),
            retry_after_seconds=int(cast("int", values[1])),
        )


async def admit_websocket_connection(user_id: UUID) -> WebSocketAdmissionDecision:
    """Apply the configured shared account admission policy.

    Parses the environment-derived rate, delegates one atomic decision to the dedicated Valkey
    store, and uses coordinated time only when a test explicitly installs it.

    Arguments:
        user_id: Immutable authenticated account identifier.

    Returns:
        Shared admission decision and retry delay.

    Raises:
        ValueError: If the configured rate is invalid.
        WebSocketAdmissionStoreError: If the store cannot decide.
    """
    limit, window_seconds = parse_throttle_rate(settings.WEBSOCKET_CONNECTION_THROTTLE_RATE)
    store = WebSocketConnectionAdmissionStore.from_settings()

    return await store.admit(
        user_id,
        limit=limit,
        window_seconds=window_seconds,
        now_milliseconds=TEST_SERVER_TIME_MILLISECONDS,
    )


class WebSocketConnectionAdmissionMiddleware:
    """Enforce shared per-user connection admission after authentication.

    Wraps an authenticated ASGI application, waits a bounded time for the shared Valkey decision,
    and maps exhaustion or store failure to their central close outcomes before consumer startup.

    Attributes:
        application: Protected consumer router reached only after admission.

    Members:
        _close_unavailable: Log and close a failed admission decision.
        __call__: Admit, throttle, or fail one authenticated connection.
    """

    def __init__(self, application: ASGI3Application) -> None:
        """Store the protected authenticated application.

        Keeps admission outside the consumer so a rejected attempt never creates group membership
        or begins application message handling.

        Arguments:
            application: Inner ASGI application reached after admission.

        Returns:
            None.
        """
        self.application = application

    async def __call__(
        self,
        scope: Scope,
        receive: ASGIReceiveCallable,
        send: ASGISendCallable,
    ) -> None:
        """Apply the shared account budget before consumer connection work.

        Counts only successfully authenticated connection attempts, rejects without entering the
        consumer when the epoch-aligned window is full, and fails closed on timeout or store loss.

        Arguments:
            scope: Authenticated WebSocket scope.
            receive: Callable yielding connection events.
            send: Callable emitting connection events.

        Returns:
            None.
        """
        scope_values = cast("dict[str, object]", scope)
        user = cast("User", scope_values["user"])
        identifier = cast("str", scope_values["connection_request_id"])
        try:
            decision = await asyncio.wait_for(
                admit_websocket_connection(user.pk),
                timeout=settings.WEBSOCKET_CONNECTION_ADMISSION_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            await self._close_unavailable(receive, send, identifier)
            return
        except WebSocketAdmissionStoreError:
            await self._close_unavailable(receive, send, identifier)
            return

        if not decision.admitted:
            await close_rejected_handshake(receive, send, WebSocketOutcome.CONNECTION_THROTTLED)
            return

        await self.application(scope, receive, send)

    @staticmethod
    async def _close_unavailable(
        receive: ASGIReceiveCallable,
        send: ASGISendCallable,
        identifier: str,
    ) -> None:
        """Log and close one unavailable admission decision.

        Shares the exact timeout and store-failure behavior while excluding exception metadata and
        connection material from the correlated log record.

        Arguments:
            receive: Callable yielding the initial connection event.
            send: Callable emitting connection events.
            identifier: Connection request identifier.

        Returns:
            None.
        """
        logger.log(
            logging.ERROR,
            "websocket connection admission unavailable",
            extra={REQUEST_ID_META_KEY: identifier},
        )
        await close_rejected_handshake(receive, send, WebSocketOutcome.SERVER_ERROR)
