"""Public registration response timing.

Provides the monotonic minimum-duration seam used only after an accepted registration outcome, with
clock and sleeper injection so every branch remains deterministic under unit coverage.
"""

from __future__ import annotations

import math
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable


def monotonic_now(*, clock: Callable[[], float] = time.monotonic) -> float:
    """Read the monotonic clock for one registration request.

    Isolates clock acquisition from the view so tests can establish the exact request boundary
    without patching process-wide time functions.

    Arguments:
        clock: Monotonic clock implementation.

    Returns:
        Current monotonic timestamp in seconds.
    """
    return clock()


def wait_for_minimum_registration_duration(
    started_at: float,
    minimum_duration_seconds: float,
    *,
    clock: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> None:
    """Wait only for the unspent portion of the public registration floor.

    Subtracts completed validation, hashing, persistence, and activation work from the configured
    duration, leaving slower real work untouched rather than adding a fixed delay to every request.

    Arguments:
        started_at: Monotonic timestamp captured at the earliest view boundary.
        minimum_duration_seconds: Configured public response floor in seconds.
        clock: Monotonic clock implementation.
        sleeper: Function that waits for the requested number of seconds.

    Returns:
        None.

    Raises:
        ValueError: If the minimum duration is non-finite or nonpositive.
    """
    if not math.isfinite(minimum_duration_seconds) or minimum_duration_seconds <= 0:
        message = "minimum registration duration must be finite and positive"
        raise ValueError(message)

    remaining = minimum_duration_seconds - (clock() - started_at)
    if remaining > 0:
        sleeper(remaining)
