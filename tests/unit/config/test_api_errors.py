"""Unit tests for the stable REST error vocabulary.

Verifies status registration, exception defaults, and immutable public definitions independently
from middleware and endpoint dispatch.
"""

from http import HTTPStatus

import pytest

from config.api_errors import (
    ERROR_STATUS_REGISTRY,
    ErrorCode,
    ServiceUnavailable,
)


@pytest.mark.unit
def test_service_unavailable_uses_the_registered_safe_contract() -> None:
    """Keep dependency loss on one stable status and error code.

    Compares the exception defaults and central registry instead of deriving expected values from
    the exception instance.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If status, code, detail, or registry membership changes.
    """
    error = ServiceUnavailable()

    assert error.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert error.default_code == ErrorCode.SERVICE_UNAVAILABLE
    assert str(error.default_detail) == "A required service is unavailable."
    assert ERROR_STATUS_REGISTRY[HTTPStatus.SERVICE_UNAVAILABLE] == frozenset(
        {ErrorCode.SERVICE_UNAVAILABLE}
    )


@pytest.mark.unit
def test_error_status_registry_is_immutable() -> None:
    """Prevent call sites from changing the global status vocabulary.

    Verifies the public mapping exposes no item-assignment operation.
    Callers therefore cannot replace a status definition at runtime.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the registry exposes item assignment.
    """
    assert hasattr(ERROR_STATUS_REGISTRY, "__setitem__") is False
