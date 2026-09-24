"""Unit tests for the public response timing seam.

Exercises clock capture and both wait branches with injected callables, providing deterministic
coverage without making the unit suite sleep.
"""

import pytest

from accounts.response_timing import (
    monotonic_now,
    wait_for_minimum_response_duration,
)

EXPECTED_TIMESTAMP = 14.25


@pytest.mark.unit
def test_monotonic_now_uses_the_injected_clock() -> None:
    """Return the timestamp supplied by the timing source.

    Injects a fixed monotonic clock so request-boundary capture is covered without depending on
    process time.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the helper bypasses the injected clock.
    """
    assert monotonic_now(clock=lambda: EXPECTED_TIMESTAMP) == EXPECTED_TIMESTAMP


@pytest.mark.unit
def test_response_duration_waits_only_for_the_remaining_floor() -> None:
    """Sleep for exactly the unspent portion of the response floor.

    Supplies deterministic start and completion timestamps and captures the requested wait,
    proving completed work is subtracted rather than followed by a fixed delay.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the helper waits for any duration other than the remainder.
    """
    waits: list[float] = []

    wait_for_minimum_response_duration(
        10.0,
        0.2,
        clock=lambda: 10.125,
        sleeper=waits.append,
    )

    assert waits == [pytest.approx(0.075)]


@pytest.mark.unit
@pytest.mark.parametrize("completed_at", [10.225, 10.275])
def test_response_duration_never_waits_after_real_work_reaches_the_floor(
    completed_at: float,
) -> None:
    """Leave floor-length and slower real work untouched.

    Covers exact and exceeded durations with a sleeper that records calls, proving the timing seam
    never adds delay after work has already consumed the configured minimum.

    Arguments:
        completed_at: Monotonic completion timestamp at or beyond the floor.

    Returns:
        None.

    Raises:
        AssertionError: If the helper sleeps after the floor has already elapsed.
    """
    waits: list[float] = []

    wait_for_minimum_response_duration(
        10.0,
        0.2,
        clock=lambda: completed_at,
        sleeper=waits.append,
    )

    assert waits == []


@pytest.mark.unit
@pytest.mark.parametrize(
    "minimum_duration_seconds",
    [
        pytest.param(float("nan"), id="nan"),
        pytest.param(float("+inf"), id="positive-infinity"),
        pytest.param(float("-inf"), id="negative-infinity"),
        pytest.param(0.0, id="zero"),
    ],
)
def test_response_duration_rejects_an_invalid_floor_deterministically(
    minimum_duration_seconds: float,
) -> None:
    """Reject an invalid response floor before reading time or sleeping.

    Supplies every non-finite float and zero with recording callables, proving defensive use of the
    helper raises consistently rather than silently skipping or requesting an unbounded sleep.

    Arguments:
        minimum_duration_seconds: Invalid response floor supplied to the helper.

    Returns:
        None.

    Raises:
        AssertionError: If the helper reads time, sleeps, or accepts an invalid floor.
    """
    waits: list[float] = []

    with pytest.raises(ValueError, match="must be finite and positive"):
        wait_for_minimum_response_duration(
            10.0,
            minimum_duration_seconds,
            clock=lambda: pytest.fail("invalid floor read the clock"),
            sleeper=waits.append,
        )

    assert waits == []
