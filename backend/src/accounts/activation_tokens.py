"""Signed activation-token primitives.

Defines one salt, payload shape, digest, and lifetime calculation shared by issuance,
confirmation, queue publication, and delayed task execution.
"""

import math
import secrets
import time
from hashlib import sha256
from typing import cast

from django.conf import settings
from django.core import signing

ACTIVATION_SIGNING_SALT = "localforge.accounts.activation"


def create_activation_token(subject_id: str) -> str:
    """Create one timestamp-signed activation bearer value.

    Binds an immutable account subject and random nonce under the project salt so each issuance is
    independently revocable while confirmation and delivery can authenticate the same shape.

    Arguments:
        subject_id: Immutable account subject identifier.

    Returns:
        Timestamp-signed activation token.
    """
    return signing.dumps(
        {"account": subject_id, "nonce": secrets.token_urlsafe(32)},
        salt=ACTIVATION_SIGNING_SALT,
    )


def activation_token_digest(token: str) -> str:
    """Digest one signed activation token.

    Produces the fixed-length one-way value persisted in authoritative state so a database read
    cannot recover the bearer credential.

    Arguments:
        token: Signed activation token.

    Returns:
        Hexadecimal SHA-256 digest.
    """
    return sha256(token.encode()).hexdigest()


def load_activation_payload(token: str) -> tuple[str, str] | None:
    """Authenticate and age-check one activation payload.

    Uses the same project salt and configured maximum age for confirmation and delayed delivery,
    returning no subject for a valid signature whose decoded shape is not an issued token.

    Arguments:
        token: Signed activation token.

    Returns:
        Immutable subject and nonce, or None for an invalid decoded payload shape.

    Raises:
        SignatureExpired: If the signed timestamp exceeded the configured lifetime.
        BadSignature: If the signed value cannot be authenticated or decoded.
    """
    payload = signing.loads(
        token,
        salt=ACTIVATION_SIGNING_SALT,
        max_age=settings.ACCOUNT_ACTIVATION_TOKEN_LIFETIME_SECONDS,
    )
    if (
        not isinstance(payload, dict)
        or not isinstance(payload.get("account"), str)
        or not isinstance(payload.get("nonce"), str)
    ):
        return None
    return cast("str", payload["account"]), cast("str", payload["nonce"])


def activation_token_remaining_seconds(token: str) -> int:
    """Calculate the remaining signed-token lifetime.

    Authenticates the token first, then reads Django's timestamp segment and rounds remaining time
    upward so broker expiry never truncates an otherwise-valid final fractional second.

    Arguments:
        token: Signed activation token.

    Returns:
        Whole seconds remaining before confirmation rejects the token.

    Raises:
        SignatureExpired: If the token is already expired.
        BadSignature: If the token cannot be authenticated or has no timestamp segment.
        ValueError: If the timestamp segment is invalid.
    """
    load_activation_payload(token)
    timestamp_segment = token.rsplit(":", 2)[-2]
    issued_at = signing.b62_decode(timestamp_segment)
    lifetime = cast("int", settings.ACCOUNT_ACTIVATION_TOKEN_LIFETIME_SECONDS)
    remaining = lifetime - (time.time() - issued_at)
    return max(0, math.ceil(remaining))
