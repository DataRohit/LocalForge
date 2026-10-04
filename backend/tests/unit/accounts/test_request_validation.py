"""Unit tests for shared account request validation.

Covers strict undeclared-field rejection and canonical email validation through
the interface every account serializer uses.
"""

import pytest
from rest_framework.exceptions import ErrorDetail, ValidationError
from rest_framework.serializers import CharField

from accounts.request_validation import StrictRequestSerializer, normalise_valid_email


class ProbeSerializer(StrictRequestSerializer):
    """Expose one declared field through strict request validation.

    Inherits from ``StrictRequestSerializer`` and adds one ordinary text field, allowing the shared
    policy to be observed without any endpoint-specific validation.

    Attributes:
        name: Single declared request field.

    Members:
        None beyond those inherited from ``StrictRequestSerializer``.
    """

    name = CharField()


@pytest.mark.unit
def test_strict_request_validation_accepts_only_declared_fields() -> None:
    """Accept declared fields and reject every undeclared field.

    Exercises success and failure through DRF's public serializer interface, requiring one stable
    detail entry for each extra key without changing declared validation.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If declared data changes or undeclared data is accepted.
    """
    accepted = ProbeSerializer(data={"name": "accepted"})

    assert accepted.is_valid()
    assert accepted.validated_data == {"name": "accepted"}

    rejected = ProbeSerializer(data={"name": "accepted", "admin": True, "unknown": "value"})

    assert not rejected.is_valid()
    assert rejected.errors == {
        "admin": [ErrorDetail("This field is not allowed.", code="invalid")],
        "unknown": [ErrorDetail("This field is not allowed.", code="invalid")],
    }

    non_object = ProbeSerializer(data=[])

    assert not non_object.is_valid()
    assert "non_field_errors" in non_object.errors


@pytest.mark.unit
def test_normalized_email_validation_is_canonical_and_strict() -> None:
    """Canonicalize valid addresses and reject invalid addresses.

    Uses the same callable endpoint serializers invoke, proving case normalization and framework
    validation remain one shared policy.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If normalization or invalid-address classification changes.
    """
    assert normalise_valid_email(" Person@Example.COM ") == "person@example.com"

    with pytest.raises(ValidationError):
        normalise_valid_email("not-an-address")
