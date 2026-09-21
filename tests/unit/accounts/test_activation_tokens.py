"""Unit tests for signed activation-token primitives.

Exercises issuance, authenticated payload loading, digest-only persistence, invalid decoded shape,
and deterministic remaining lifetime without a database.
"""

from hashlib import sha256

import pytest
from django.core import signing
from freezegun import freeze_time

from accounts.activation_tokens import (
    ACTIVATION_SIGNING_SALT,
    activation_token_digest,
    activation_token_remaining_seconds,
    create_activation_token,
    load_activation_payload,
)


@pytest.mark.unit
@freeze_time("2026-09-21 00:00:00+00:00")
def test_activation_token_round_trips_without_persisting_the_bearer() -> None:
    """Issue and authenticate one activation credential.

    Verifies the immutable subject survives signing, the nonce is non-empty, and the stored value
    is the independent SHA-256 digest.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If payload, digest, or remaining lifetime differs.
    """
    token = create_activation_token("account-subject")
    payload = load_activation_payload(token)

    assert payload is not None
    assert payload[0] == "account-subject"
    assert payload[1]
    assert activation_token_digest(token) == sha256(token.encode()).hexdigest()
    assert activation_token_remaining_seconds(token) > 0


@pytest.mark.unit
def test_activation_payload_rejects_a_valid_signature_with_the_wrong_shape() -> None:
    """Reject signed data that was not issued by the activation protocol.

    Signs a list under the correct salt and verifies authentication alone cannot turn it into a
    usable subject.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If an invalid payload shape is accepted.
    """
    token = signing.dumps(["account-subject"], salt=ACTIVATION_SIGNING_SALT)

    assert load_activation_payload(token) is None
