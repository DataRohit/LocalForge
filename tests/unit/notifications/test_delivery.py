"""Unit tests for notification group addressing.

Verifies the deterministic channel-safe name derived from an immutable account identifier without
opening a channel-layer connection.
"""

import re
import uuid
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, Mock

import pytest

from notifications.delivery import (
    MAXIMUM_NOTIFICATION_INTEGER,
    MINIMUM_NOTIFICATION_INTEGER,
    InvalidNotificationDataError,
    InvalidNotificationRecipientError,
    JsonValue,
    notification_group_name,
    publish_notification,
)

pytestmark = pytest.mark.unit


def test_notification_group_name_is_deterministic_and_channel_safe() -> None:
    """Derive one stable channel-safe group from an immutable account identifier.

    Uses a fixed UUID as the independent source of the exact expected name and checks the complete
    result against Channels' documented character and length constraints.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If naming is unstable, lossy, or unsafe for the channel layer.
    """
    account_id = uuid.UUID("018f22e2-7d42-7f74-9d8a-123456789abc")

    first = notification_group_name(account_id)
    second = notification_group_name(account_id)

    assert first == "notifications.018f22e27d427f749d8a123456789abc"
    assert second == first
    assert re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", first)


@pytest.mark.parametrize(
    "invalid_value",
    [float("nan"), float("inf"), float("-inf")],
    ids=["nan", "positive-infinity", "negative-infinity"],
)
def test_publish_notification_rejects_non_finite_data_before_dispatch(
    monkeypatch: pytest.MonkeyPatch,
    invalid_value: float,
) -> None:
    """Reject every non-finite float before acquiring group delivery.

    Calls the synchronous public publisher with each Python float spelling that JSON forbids and
    observes the channel-layer boundary independently, proving no event is dispatched.

    Arguments:
        monkeypatch: Fixture replacing the configured channel layer with an observation seam.
        invalid_value: Non-finite float the strict JSON contract rejects.

    Returns:
        None.

    Raises:
        AssertionError: If validation permits publication or raises another public exception.
    """
    group_send = AsyncMock()
    layer = SimpleNamespace(group_send=group_send)
    monkeypatch.setattr("notifications.delivery.get_channel_layer", lambda: layer)

    with pytest.raises(InvalidNotificationDataError):
        publish_notification(
            uuid.uuid4(),
            "numeric.invalid",
            {"value": invalid_value},
        )

    group_send.assert_not_awaited()


def test_publish_notification_rejects_non_json_data_before_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject a non-JSON object before acquiring group delivery.

    Bypasses static typing deliberately at the public Python boundary and verifies runtime
    validation contains unsupported values without sending any channel event.

    Arguments:
        monkeypatch: Fixture replacing the configured channel layer with an observation seam.

    Returns:
        None.

    Raises:
        AssertionError: If unsupported data reaches the channel layer.
    """
    group_send = AsyncMock()
    layer = SimpleNamespace(group_send=group_send)
    monkeypatch.setattr("notifications.delivery.get_channel_layer", lambda: layer)
    invalid_data = cast(
        "dict[str, JsonValue]",
        cast("object", {"value": object()}),
    )

    with pytest.raises(InvalidNotificationDataError):
        publish_notification(
            uuid.uuid4(),
            "object.invalid",
            invalid_data,
        )

    group_send.assert_not_awaited()


@pytest.mark.parametrize(
    "accepted_value",
    [MINIMUM_NOTIFICATION_INTEGER, MAXIMUM_NOTIFICATION_INTEGER],
    ids=["minimum", "maximum"],
)
def test_publish_notification_delivers_transport_integer_extremes(
    monkeypatch: pytest.MonkeyPatch,
    accepted_value: int,
) -> None:
    """Deliver the minimum and maximum integers the configured serializer preserves.

    Calls the synchronous public publisher with both independently measured MessagePack limits and
    observes the channel boundary, proving each accepted extreme is dispatched unchanged.

    Arguments:
        monkeypatch: Fixture replacing the configured channel layer with an observation seam.
        accepted_value: Inclusive transport-compatible integer boundary.

    Returns:
        None.

    Raises:
        AssertionError: If either supported integer is rejected, changed, or not dispatched.
    """
    user_id = uuid.uuid4()
    group_send = AsyncMock()
    layer = SimpleNamespace(group_send=group_send)
    monkeypatch.setattr("notifications.delivery.get_channel_layer", lambda: layer)

    publish_notification(
        user_id,
        "numeric.boundary",
        {"nested": [{"value": accepted_value}]},
    )

    group_send.assert_awaited_once_with(
        notification_group_name(user_id),
        {
            "type": "notification.message",
            "event": "numeric.boundary",
            "data": {"nested": [{"value": accepted_value}]},
        },
    )


@pytest.mark.parametrize(
    "rejected_value",
    [MINIMUM_NOTIFICATION_INTEGER - 1, MAXIMUM_NOTIFICATION_INTEGER + 1],
    ids=["first-negative-rejection", "first-positive-rejection"],
)
def test_publish_notification_rejects_first_out_of_range_integers_before_dispatch(
    monkeypatch: pytest.MonkeyPatch,
    rejected_value: int,
) -> None:
    """Reject the first integers outside the configured serializer's range.

    Places each value inside nested JSON containers so recursive validation is required, then
    proves the stable public exception prevents any channel-layer dispatch.

    Arguments:
        monkeypatch: Fixture replacing the configured channel layer with an observation seam.
        rejected_value: First unsupported integer beyond one inclusive boundary.

    Returns:
        None.

    Raises:
        AssertionError: If the transport-specific overflow escapes or an event is dispatched.
    """
    group_send = AsyncMock()
    layer = SimpleNamespace(group_send=group_send)
    monkeypatch.setattr("notifications.delivery.get_channel_layer", lambda: layer)

    with pytest.raises(InvalidNotificationDataError):
        publish_notification(
            uuid.uuid4(),
            "numeric.out_of_range",
            {"nested": [{"value": rejected_value}]},
        )

    group_send.assert_not_awaited()


@pytest.mark.parametrize(
    ("key", "nested"),
    [
        (1, False),
        (1, True),
        (MINIMUM_NOTIFICATION_INTEGER - 1, False),
        (MINIMUM_NOTIFICATION_INTEGER - 1, True),
        (MAXIMUM_NOTIFICATION_INTEGER + 1, False),
        (MAXIMUM_NOTIFICATION_INTEGER + 1, True),
    ],
    ids=[
        "ordinary-top-level",
        "ordinary-nested",
        "out-of-range-negative-top-level",
        "out-of-range-negative-nested",
        "out-of-range-positive-top-level",
        "out-of-range-positive-nested",
    ],
)
def test_publish_notification_rejects_non_string_object_keys_before_dispatch(
    monkeypatch: pytest.MonkeyPatch,
    key: int,
    *,
    nested: bool,
) -> None:
    """Reject ordinary and out-of-range integer keys at every object depth.

    Bypasses the static string-key type at the public Python boundary and verifies strict JSON
    object semantics prevent both silent key coercion and MessagePack failures before dispatch.

    Arguments:
        monkeypatch: Fixture replacing the configured channel layer with an observation seam.
        key: Integer mapping key the public boundary must reject.
        nested: Whether the invalid key appears in the root object or a nested object.

    Returns:
        None.

    Raises:
        AssertionError: If a non-string key is coerced, dispatched, or raises another exception.
    """
    group_send = AsyncMock()
    layer = SimpleNamespace(group_send=group_send)
    monkeypatch.setattr("notifications.delivery.get_channel_layer", lambda: layer)
    raw_data: dict[object, object] = {"nested": {key: "value"}} if nested else {key: "value"}
    invalid_data = cast("dict[str, JsonValue]", cast("object", raw_data))

    with pytest.raises(InvalidNotificationDataError):
        publish_notification(
            uuid.uuid4(),
            "object.invalid_key",
            invalid_data,
        )

    group_send.assert_not_awaited()


@pytest.mark.parametrize(
    "raw_data",
    [
        (1,),
        {"nested": (1,)},
        (MINIMUM_NOTIFICATION_INTEGER - 1,),
        {"nested": (MINIMUM_NOTIFICATION_INTEGER - 1,)},
        (MAXIMUM_NOTIFICATION_INTEGER + 1,),
        {"nested": (MAXIMUM_NOTIFICATION_INTEGER + 1,)},
    ],
    ids=[
        "top-level-valid-looking",
        "nested-valid-looking",
        "top-level-negative-overflow",
        "nested-negative-overflow",
        "top-level-positive-overflow",
        "nested-positive-overflow",
    ],
)
def test_publish_notification_rejects_tuples_before_dispatch(
    monkeypatch: pytest.MonkeyPatch,
    raw_data: object,
) -> None:
    """Reject top-level and nested tuples regardless of their apparent contents.

    Exercises ordinary and transport-overflowing tuple members so Python's permissive JSON array
    coercion can never hide an out-of-domain container from the public publisher.

    Arguments:
        monkeypatch: Fixture replacing the configured channel layer with an observation seam.
        raw_data: Top-level or nested tuple-bearing data supplied to the publisher.

    Returns:
        None.

    Raises:
        AssertionError: If a tuple is normalized, dispatched, or raises another exception.
    """
    group_send = AsyncMock()
    layer = SimpleNamespace(group_send=group_send)
    monkeypatch.setattr("notifications.delivery.get_channel_layer", lambda: layer)
    invalid_data = cast("dict[str, JsonValue]", raw_data)

    with pytest.raises(InvalidNotificationDataError):
        publish_notification(
            uuid.uuid4(),
            "container.invalid",
            invalid_data,
        )

    group_send.assert_not_awaited()


@pytest.mark.parametrize(
    ("event", "raw_data"),
    [
        ("\ud800", {}),
        ("unicode.invalid", {"value": "\ud800"}),
        ("unicode.invalid", {"nested": {"value": "\ud800"}}),
        ("unicode.invalid", {"\ud800": "value"}),
        ("unicode.invalid", {"nested": {"\ud800": "value"}}),
    ],
    ids=[
        "event",
        "top-level-value",
        "nested-value",
        "top-level-key",
        "nested-key",
    ],
)
def test_publish_notification_rejects_surrogate_strings_before_dispatch(
    monkeypatch: pytest.MonkeyPatch,
    event: str,
    raw_data: dict[object, object],
) -> None:
    """Reject surrogate event, key, and value strings at every object depth.

    Bypasses static key/value typing where required and proves UTF-8 validation contains every
    MessagePack encoding failure behind the stable public exception before dispatch.

    Arguments:
        monkeypatch: Fixture replacing the configured channel layer with an observation seam.
        event: Event name under test.
        raw_data: Top-level or nested surrogate-bearing data.

    Returns:
        None.

    Raises:
        AssertionError: If a surrogate reaches the channel layer.
    """
    group_send = AsyncMock()
    layer = SimpleNamespace(group_send=group_send)
    monkeypatch.setattr("notifications.delivery.get_channel_layer", lambda: layer)
    invalid_data = cast("dict[str, JsonValue]", cast("object", raw_data))

    with pytest.raises(InvalidNotificationDataError):
        publish_notification(
            uuid.uuid4(),
            event,
            invalid_data,
        )

    group_send.assert_not_awaited()


@pytest.mark.parametrize(
    "raw_data",
    [
        [],
        "text",
        1,
        1.5,
        True,
        None,
        (1,),
        object(),
    ],
    ids=[
        "list",
        "string",
        "integer",
        "finite-float",
        "boolean",
        "none",
        "tuple",
        "unsupported-object",
    ],
)
def test_publish_notification_rejects_every_non_object_data_root_before_dispatch(
    monkeypatch: pytest.MonkeyPatch,
    raw_data: object,
) -> None:
    """Require the public data root to be a dictionary.

    Bypasses static typing with every JSON-native non-object root plus tuple and unsupported object
    controls, proving arrays and scalars remain valid only when nested inside the root object.

    Arguments:
        monkeypatch: Fixture replacing the configured channel layer with an observation seam.
        raw_data: Non-dictionary root supplied to the public publisher.

    Returns:
        None.

    Raises:
        AssertionError: If a non-object root is dispatched or raises another public exception.
    """
    group_send = AsyncMock()
    layer = SimpleNamespace(group_send=group_send)
    monkeypatch.setattr("notifications.delivery.get_channel_layer", lambda: layer)
    invalid_data = cast("dict[str, JsonValue]", raw_data)

    with pytest.raises(InvalidNotificationDataError):
        publish_notification(
            uuid.uuid4(),
            "root.invalid",
            invalid_data,
        )

    group_send.assert_not_awaited()


@pytest.mark.parametrize(
    "invalid_user_id",
    [
        None,
        "018f22e2-7d42-7f74-9d8a-123456789abc",
        7,
        1.5,
        b"uuid-bytes",
        SimpleNamespace(hex="not channel safe !"),
        SimpleNamespace(hex=lambda: "callable-is-not-an-id"),
    ],
    ids=[
        "none",
        "canonical-uuid-string",
        "integer",
        "float",
        "bytes",
        "unsafe-hex-attribute",
        "callable-hex-attribute",
    ],
)
def test_publish_notification_rejects_non_uuid_recipients_before_layer_acquisition(
    monkeypatch: pytest.MonkeyPatch,
    invalid_user_id: object,
) -> None:
    """Reject every recipient identity that is not an actual UUID instance.

    Exercises common scalar IDs and crafted UUID-like objects so neither attribute lookup nor
    process-specific representations can influence group naming or reach the channel layer.

    Arguments:
        monkeypatch: Fixture replacing channel-layer acquisition with an observation seam.
        invalid_user_id: Runtime recipient value outside the public UUID contract.

    Returns:
        None.

    Raises:
        AssertionError: If validation acquires the channel layer or dispatches an event.
    """
    group_send = AsyncMock()
    layer = SimpleNamespace(group_send=group_send)
    layer_factory = Mock(return_value=layer)
    monkeypatch.setattr("notifications.delivery.get_channel_layer", layer_factory)

    with pytest.raises(InvalidNotificationRecipientError):
        publish_notification(
            cast("uuid.UUID", invalid_user_id),
            "recipient.invalid",
            {},
        )

    layer_factory.assert_not_called()
    group_send.assert_not_awaited()
