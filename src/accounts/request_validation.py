"""Shared account request validation policy.

Rejects undeclared request fields and canonicalizes email addresses through
one interface used by authentication and account lifecycle serializers.
"""

# mypy: disable-error-code=misc

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast, override

from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import validate_email
from rest_framework.exceptions import ValidationError
from rest_framework.serializers import Serializer

from accounts.normalisation import normalise_email


def normalise_valid_email(value: str) -> str:
    """Normalize and validate one public email value.

    Applies the project's canonical storage form before Django's validator runs, preventing a
    Unicode case transformation from turning accepted input into an invalid persisted address.

    Arguments:
        value: Address that passed DRF's initial email validation.

    Returns:
        Valid normalized address.

    Raises:
        ValidationError: If the normalized address is invalid.
    """
    normalized = normalise_email(value)
    try:
        validate_email(normalized)
    except DjangoValidationError as error:
        raise ValidationError(error.messages) from error

    return normalized


class StrictRequestSerializer(Serializer):
    """Reject every request key outside a serializer's declared contract.

    Inherits from DRF's ``Serializer`` and converts unknown keys into field-specific validation
    details before normal field validation, preventing silent identity-selector spoofing.

    Attributes:
        None beyond those inherited from ``Serializer``.

    Members:
        to_internal_value: Reject undeclared input keys.
    """

    @override
    def to_internal_value(self, data: object) -> dict[str, Any]:
        """Reject undeclared keys before validating declared fields.

        Preserves DRF's normal non-object handling and returns one stable detail entry for every
        unexpected mapping key.

        Arguments:
            data: Raw parsed request representation.

        Returns:
            Validated native field mapping.

        Raises:
            ValidationError: If the input carries an undeclared field.
        """
        if isinstance(data, Mapping):
            unexpected = sorted(str(key) for key in data if key not in self.fields)
            if unexpected:
                raise ValidationError(
                    {field: ["This field is not allowed."] for field in unexpected}
                )

        return cast("dict[str, Any]", super().to_internal_value(data))
