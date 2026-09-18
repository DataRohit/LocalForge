"""Unit tests for token-login admission helpers.

Exercises rate parsing, invalid authoritative-store inputs, and retry defaults without touching
PostgreSQL, Valkey, or any other external service.
"""

import pytest

from accounts.login_throttle import PostgresLoginThrottleStore, RollingWindowRule
from accounts.models import LoginThrottleEvent
from accounts.request_throttling import parse_throttle_rate
from accounts.token_authentication import TokenLoginThrottle


@pytest.mark.unit
@pytest.mark.parametrize(
    ("rate", "expected"),
    [
        pytest.param("2/second", (2, 1), id="second"),
        pytest.param("3/minute", (3, 60), id="minute"),
        pytest.param("4/hour", (4, 3600), id="hour"),
        pytest.param("5/day", (5, 86400), id="day"),
    ],
)
def test_strict_throttle_rate_parser_accepts_documented_periods(
    rate: str,
    expected: tuple[int, int],
) -> None:
    """Parse every documented strict throttle period.

    Verifies the environment contract resolves each supported first-letter period to an independent
    literal duration.

    Arguments:
        rate: Configured count and period.
        expected: Independent count and duration pair.

    Returns:
        None.

    Raises:
        AssertionError: If parsing changes the documented rate.
    """
    assert parse_throttle_rate(rate) == expected


@pytest.mark.unit
@pytest.mark.parametrize(
    "rate",
    [
        pytest.param("", id="empty"),
        pytest.param("one/minute", id="non-numeric"),
        pytest.param("1/week", id="unsupported-period"),
        pytest.param("0/minute", id="zero"),
        pytest.param("-1/minute", id="negative"),
    ],
)
def test_strict_throttle_rate_parser_rejects_invalid_values(rate: str) -> None:
    """Reject malformed or non-positive strict throttle rates.

    Exercises configuration errors through the public parser so invalid limits cannot silently
    disable authentication controls.

    Arguments:
        rate: Invalid configured rate.

    Returns:
        None.

    Raises:
        AssertionError: If invalid configuration is accepted.
    """
    with pytest.raises(ValueError, match="strict throttle"):
        parse_throttle_rate(rate)


@pytest.mark.unit
def test_login_throttle_store_requires_at_least_one_rule() -> None:
    """Reject an admission request with no security dimension.

    Calls the public store before any database access and verifies it refuses to manufacture an
    unbounded admission decision.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If an empty decision is accepted.
    """
    store = PostgresLoginThrottleStore("default")

    with pytest.raises(ValueError, match="at least one"):
        store.admit((), member="request-id")


@pytest.mark.unit
@pytest.mark.parametrize(
    "rule",
    [
        pytest.param(
            RollingWindowRule(key="address", limit=0, window_seconds=60),
            id="zero-limit",
        ),
        pytest.param(
            RollingWindowRule(key="address", limit=1, window_seconds=0),
            id="zero-window",
        ),
        pytest.param(
            RollingWindowRule(key="address", limit=1, window_seconds=86401),
            id="beyond-retention",
        ),
    ],
)
def test_login_throttle_store_rejects_non_positive_rules(rule: RollingWindowRule) -> None:
    """Reject non-positive admission limits and windows.

    Passes each invalid rule through the public store and verifies validation happens before a
    database connection could turn bad configuration into an outage response.

    Arguments:
        rule: Invalid rolling-window rule.

    Returns:
        None.

    Raises:
        AssertionError: If invalid configuration reaches PostgreSQL.
    """
    store = PostgresLoginThrottleStore("default")

    with pytest.raises(ValueError, match="positive"):
        store.admit((rule,), member="request-id")


@pytest.mark.unit
def test_login_throttle_store_bounds_rows_inserted_per_admission() -> None:
    """Reject more dimensions than bounded cleanup can safely outpace.

    Supplies four otherwise valid rules and verifies validation occurs before database access, so
    every accepted call can insert at most three rows while cleanup may remove sixty-four.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If an unbounded number of dimensions reaches persistence.
    """
    store = PostgresLoginThrottleStore("default")
    rules = tuple(
        RollingWindowRule(key=f"dimension:{index}", limit=1, window_seconds=60)
        for index in range(4)
    )

    with pytest.raises(ValueError, match="at most three"):
        store.admit(rules, member="request-id")


@pytest.mark.unit
def test_login_throttle_store_requires_distinct_dimension_keys() -> None:
    """Reject duplicate dimensions before they can violate persistence uniqueness.

    Supplies two policies for one opaque bucket and verifies the public store refuses an ambiguous
    decision rather than inserting duplicate rows or making cleanup-rate reasoning inaccurate.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If duplicate dimensions reach PostgreSQL.
    """
    store = PostgresLoginThrottleStore("default")
    rules = (
        RollingWindowRule(key="address", limit=1, window_seconds=60),
        RollingWindowRule(key="address", limit=2, window_seconds=60),
    )

    with pytest.raises(ValueError, match="unique"):
        store.admit(rules, member="request-id")


@pytest.mark.unit
def test_login_throttle_has_no_retry_before_a_denial() -> None:
    """Return no retry estimate before shared state rejects a request.

    Exercises the public DRF throttle seam directly so a newly constructed instance does not invent
    a delay that could become an incorrect protocol header.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If a fresh throttle reports a retry delay.
    """
    assert TokenLoginThrottle().wait() is None


@pytest.mark.unit
def test_login_throttle_event_renders_only_opaque_identifiers() -> None:
    """Render persisted admission state without recovering submitted identity.

    Constructs the model without saving it and verifies its diagnostic text contains only the
    already-opaque bucket and correlated request identifier.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If model text loses either opaque identifier.
    """
    event = LoginThrottleEvent(bucket="address:digest", request_id="request-id")

    assert str(event) == "address:digest:request-id"
