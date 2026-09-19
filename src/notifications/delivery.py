"""User-targeted notification delivery.

Derives one channel-safe address from an immutable account identifier and provides the synchronous
publishing seam used by ordinary application views and tasks.
"""

from __future__ import annotations

import json
from typing import Literal, TypedDict, cast
from uuid import UUID

from asgiref.sync import async_to_sync
from channels.layers import BaseChannelLayer, get_channel_layer

NOTIFICATION_GROUP_PREFIX = "notifications"
MINIMUM_NOTIFICATION_INTEGER = -(2**63)
MAXIMUM_NOTIFICATION_INTEGER = (2**64) - 1

type JsonValue = bool | int | float | str | list[JsonValue] | dict[str, JsonValue] | None

JSON_DATA_ERRORS = (RecursionError, TypeError, ValueError)


class InvalidNotificationDataError(ValueError):
    """Signal that notification data cannot be represented as strict JSON.

    Inherits from ``ValueError`` and carries no rejected value, preventing application data from
    entering exception text while giving synchronous callers one stable validation failure.

    Attributes:
        None.

    Members:
        None.
    """


class InvalidNotificationRecipientError(ValueError):
    """Signal that a notification recipient is not an immutable UUID.

    Inherits from ``ValueError`` and carries no rejected identifier, keeping arbitrary objects and
    their representations out of exception text while exposing one stable caller-facing failure.

    Attributes:
        None.

    Members:
        None.
    """


class NotificationChannelEvent(TypedDict):
    """Describe one internal channel-layer notification event.

    Inherits from ``TypedDict`` and fixes the dispatch name plus the client-visible event and data
    fields shared by the synchronous publisher and asynchronous consumer.

    Attributes:
        type: Channels dispatch name for notification delivery.
        event: Stable application event name exposed to the client.
        data: Event-specific JSON object exposed to the client.

    Members:
        None.
    """

    type: Literal["notification.message"]
    event: str
    data: dict[str, JsonValue]


def notification_group_name(user_id: UUID) -> str:
    """Derive the channel-layer group for one account.

    Uses the UUID hexadecimal form so the result is deterministic, contains only channel-safe
    characters, and reveals no mutable account attribute such as username or email address.

    Arguments:
        user_id: Immutable primary identifier of the account to address.

    Returns:
        Channel-safe group name used by every socket belonging to the account.

    Raises:
        InvalidNotificationRecipientError: If the identifier is not an actual UUID instance.
    """
    if not isinstance(user_id, UUID):
        raise InvalidNotificationRecipientError

    return f"{NOTIFICATION_GROUP_PREFIX}.{user_id.hex}"


def _validate_notification_string(value: object) -> None:
    """Validate one protocol string without leaking its contents.

    Requires the declared string type and verifies UTF-8 encodability before MessagePack can expose
    a codec exception from the channel layer.

    Arguments:
        value: Candidate event name, object key, or string value.

    Returns:
        None.

    Raises:
        InvalidNotificationDataError: If the value is not a string or contains a lone surrogate.
    """
    if not isinstance(value, str):
        raise InvalidNotificationDataError

    try:
        value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise InvalidNotificationDataError from error


def _validate_notification_value(value: object) -> None:
    """Validate one nested value against the JSON and transport domains.

    Accepts only the types declared by ``JsonValue``, validates every string as UTF-8, rejects
    non-string object keys, and bounds integers for the configured MessagePack serializer.

    Arguments:
        value: Candidate JSON value or container to validate.

    Returns:
        None.

    Raises:
        InvalidNotificationDataError: If the value lies outside the declared JSON-native and
            transport-compatible domain.
    """
    if isinstance(value, str):
        _validate_notification_string(value)
        return

    if value is None or isinstance(value, (bool, float)):
        return

    if isinstance(value, int) and not (
        MINIMUM_NOTIFICATION_INTEGER <= value <= MAXIMUM_NOTIFICATION_INTEGER
    ):
        raise InvalidNotificationDataError

    if isinstance(value, int):
        return

    if isinstance(value, list):
        for item in value:
            _validate_notification_value(item)
    elif isinstance(value, dict):
        for key, item in value.items():
            _validate_notification_string(key)
            _validate_notification_value(item)
    else:
        raise InvalidNotificationDataError


def _validate_notification_data(event: str, data: dict[str, JsonValue]) -> None:
    """Validate notification data with the outbound strict JSON encoder.

    Applies the declared JSON-native domain, UTF-8 string contract, channel serializer's integer
    range, and standards-compliant text JSON policy before dispatch.

    Arguments:
        event: Stable application event name supplied to the public publisher.
        data: Event-specific JSON object supplied to the public publisher.

    Returns:
        None.

    Raises:
        InvalidNotificationDataError: If the event or data cannot cross both serializers.
    """
    if not isinstance(data, dict):
        raise InvalidNotificationDataError

    try:
        _validate_notification_string(event)
        _validate_notification_value(data)
        json.dumps(data, allow_nan=False, separators=(",", ":"))
    except InvalidNotificationDataError:
        raise
    except JSON_DATA_ERRORS as error:
        raise InvalidNotificationDataError from error


def publish_notification(user_id: UUID, event: str, data: dict[str, JsonValue]) -> None:
    """Publish one notification to every current socket owned by an account.

    Bridges ordinary synchronous views and tasks to the asynchronous channel layer while hiding
    group naming and Channels event dispatch details from application callers.

    Arguments:
        user_id: Immutable primary identifier of the account to address.
        event: Stable application event name exposed to the client.
        data: Event-specific JSON object exposed to the client.

    Returns:
        None.

    Raises:
        InvalidNotificationRecipientError: If the recipient identifier is not an actual UUID.
        InvalidNotificationDataError: If the data exceeds the supported integer range or cannot
            be represented as strict JSON.
    """
    group = notification_group_name(user_id)
    _validate_notification_data(event, data)
    channel_layer = cast("BaseChannelLayer", get_channel_layer())
    channel_event = NotificationChannelEvent(
        type="notification.message",
        event=event,
        data=data,
    )
    async_to_sync(channel_layer.group_send)(
        group,
        channel_event,
    )
