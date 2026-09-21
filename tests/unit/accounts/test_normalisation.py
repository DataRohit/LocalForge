"""Unit tests for canonical account identifiers.

Checks the one pure email normalization seam used by managers, models, serializers, and recovery
flows.
"""

import pytest

from accounts.normalisation import normalise_email


@pytest.mark.unit
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (" User@LOCALFORGE.Invalid ", "user@localforge.invalid"),
        ("", ""),
        (None, ""),
    ],
)
def test_email_normalization_is_total_and_canonical(
    value: str | None,
    expected: str,
) -> None:
    """Normalize every supported input to its stored representation.

    Covers mixed case, surrounding whitespace, empty text, and absent input with independent
    expected literals.

    Arguments:
        value: Address value supplied by a caller.
        expected: Canonical stored representation.

    Returns:
        None.

    Raises:
        AssertionError: If normalization produces another representation.
    """
    assert normalise_email(value) == expected
