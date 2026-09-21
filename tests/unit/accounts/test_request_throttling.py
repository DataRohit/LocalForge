"""Unit tests for throttle-rate and client-address parsing.

Exercises valid periods, malformed configuration, trusted proxy walking, and direct ASGI address
resolution without cache or database access.
"""

from ipaddress import ip_network

import pytest
from django.test import override_settings

from accounts.request_throttling import (
    parse_throttle_rate,
    trusted_client_address_from_scope,
)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("rate", "expected"),
    [
        ("5/second", (5, 1)),
        ("30/minute", (30, 60)),
        ("3/hour", (3, 3600)),
        ("1/day", (1, 86400)),
    ],
)
def test_throttle_rates_parse_to_positive_windows(
    rate: str,
    expected: tuple[int, int],
) -> None:
    """Parse every documented period vocabulary.

    Compares independent count/window literals for the environment rate format.
    Each supported period is represented directly.

    Arguments:
        rate: Configured count-per-period value.
        expected: Parsed count and seconds.

    Returns:
        None.

    Raises:
        AssertionError: If parsing changes.
    """
    assert parse_throttle_rate(rate) == expected


@pytest.mark.unit
@pytest.mark.parametrize("rate", ["", "0/minute", "-1/hour", "5/unknown", "not-a-rate"])
def test_invalid_throttle_rates_fail_at_configuration_time(rate: str) -> None:
    """Refuse malformed or non-positive admission policy.

    Requires a stable value error instead of a silent default.
    Invalid policy must fail before any request admission.

    Arguments:
        rate: Invalid configured rate.

    Returns:
        None.

    Raises:
        ValueError: Expected for invalid policy.
    """
    with pytest.raises(
        ValueError,
        match=r"invalid strict throttle rate|strict throttle count must be positive",
    ):
        parse_throttle_rate(rate)


@pytest.mark.unit
@override_settings(TRUSTED_PROXY_NETWORKS=(ip_network("10.89.2.0/24"),))
def test_asgi_address_resolution_walks_past_trusted_hops() -> None:
    """Select the first untrusted client behind a trusted immediate peer.

    Combines repeated transport headers in order and verifies the canonical external address.
    Trusted proxy addresses must never become the bucket identity.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If a proxy address becomes the throttle identity.
    """
    scope = {
        "client": ("10.89.2.5", 50000),
        "headers": [
            (b"x-forwarded-for", b"198.51.100.44"),
            (b"x-forwarded-for", b"10.89.2.6"),
        ],
    }

    assert trusted_client_address_from_scope(scope) == "198.51.100.44"
