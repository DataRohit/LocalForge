"""Account credential verification policy.

Classifies accepted password profiles, performs enumeration-resistant
verification, and revalidates locked account state before credential issuance.
"""

from __future__ import annotations

import secrets
from base64 import b64decode
from dataclasses import dataclass
from enum import StrEnum
from functools import cache
from hashlib import sha256
from typing import TYPE_CHECKING, cast

from django.conf import settings
from django.contrib.auth.hashers import (
    Argon2PasswordHasher,
    PBKDF2PasswordHasher,
    PBKDF2SHA1PasswordHasher,
    ScryptPasswordHasher,
    check_password,
    get_hashers,
    identify_hasher,
    make_password,
)
from django.db import connections
from django.db.models import Value
from django.db.models.functions import Lower
from rest_framework.exceptions import AuthenticationFailed

from accounts.models import User

if TYPE_CHECKING:
    from uuid import UUID

    from argon2 import Parameters

MAX_ENCODED_PASSWORD_LENGTH = 128


class PasswordHashDisposition(StrEnum):
    """Describe whether a stored password profile may be verified.

    Inherits from ``StrEnum`` and separates current profiles, recognized lower PBKDF2 profiles,
    and every profile that requires an out-of-band password reset.

    Attributes:
        CURRENT: Encoding matches its configured hasher's current cost profile.
        RECOGNIZED_LOWER: PBKDF2 encoding has positive iterations below the current profile.
        RESET_REQUIRED: Encoding must not be verified during login.

    Members:
        None beyond those inherited from ``StrEnum``.
    """

    CURRENT = "current"
    RECOGNIZED_LOWER = "recognized-lower"
    RESET_REQUIRED = "reset-required"


@dataclass(frozen=True, slots=True)
class AuthenticatedCredentialSnapshot:
    """Capture account security state established by credential verification.

    Stores immutable identity and exact password encoding outside any row lock, together with an
    optional precomputed preferred encoding that issuance may persist after locked revalidation.

    Attributes:
        account_id: Immutable primary account identifier.
        encoded_password: Exact password encoding that authenticated the request.
        is_active: Active state that authenticated the request.
        replacement_encoded_password: Precomputed preferred encoding, or None when current.

    Members:
        None.
    """

    account_id: UUID
    encoded_password: str
    is_active: bool
    replacement_encoded_password: str | None


def _has_valid_base64(value: str, expected_length: int) -> bool:
    """Validate one bounded password-hash base64 component.

    Adds only syntactic padding before strict decoding and compares the decoded byte count with the
    configured digest or parsed salt length, preventing malformed encodings from reaching a hasher.

    Arguments:
        value: Base64 component without required padding.
        expected_length: Exact decoded byte count.

    Returns:
        Whether the component is strict base64 with the expected length.
    """
    try:
        decoded = b64decode(value + ("=" * (-len(value) % 4)), validate=True)
    except ValueError:
        return False

    return len(decoded) == expected_length


def _classify_pbkdf2_profile(
    hasher: PBKDF2PasswordHasher | PBKDF2SHA1PasswordHasher,
    decoded: dict[str, object],
) -> PasswordHashDisposition:
    """Classify one structurally valid PBKDF2 cost profile.

    Accepts the configured iteration count and positive lower counts whose missing work Django can
    add exactly through runtime hardening, while refusing zero, negative, and stronger counts.

    Arguments:
        hasher: Configured PBKDF2 variant.
        decoded: Parsed stored encoding metadata.

    Returns:
        Current, recognized-lower, or reset-required disposition.
    """
    digest_length = 20 if hasher.algorithm == "pbkdf2_sha1" else 32
    if not cast("str", decoded["salt"]) or not _has_valid_base64(
        cast("str", decoded["hash"]),
        digest_length,
    ):
        return PasswordHashDisposition.RESET_REQUIRED

    iterations = cast("int", decoded["iterations"])
    disposition = PasswordHashDisposition.RESET_REQUIRED
    if iterations == hasher.iterations:
        disposition = PasswordHashDisposition.CURRENT
    elif 0 < iterations < hasher.iterations:
        disposition = PasswordHashDisposition.RECOGNIZED_LOWER

    return disposition


def _argon2_profile_is_current(
    hasher: Argon2PasswordHasher,
    decoded: dict[str, object],
    encoded: str,
) -> bool:
    """Compare every bounded Argon2 verification parameter with current configuration.

    Excludes only salt length, which is stored input rather than a work parameter and remains
    bounded by the password column, while checking type, version, time, memory, lanes, and output.

    Arguments:
        hasher: Configured Argon2 hasher.
        decoded: Parsed stored encoding metadata.
        encoded: Original stored encoding carrying strict base64 components.

    Returns:
        Whether the complete accepted Argon2 profile is current.
    """
    parameters = cast("Parameters", decoded["params"])
    current = hasher.params()
    _algorithm, _variety, _version, _costs, salt, digest = encoded.split("$", 5)

    if not _has_valid_base64(salt, parameters.salt_len) or not _has_valid_base64(
        digest,
        parameters.hash_len,
    ):
        return False

    return (
        parameters.type,
        parameters.version,
        parameters.time_cost,
        parameters.memory_cost,
        parameters.parallelism,
        parameters.hash_len,
    ) == (
        current.type,
        current.version,
        current.time_cost,
        current.memory_cost,
        current.parallelism,
        current.hash_len,
    )


def _scrypt_profile_is_current(
    hasher: ScryptPasswordHasher,
    decoded: dict[str, object],
) -> bool:
    """Compare every Scrypt verification parameter with current configuration.

    Requires the work factor, block size, and parallelism to match as one atomic profile, preventing
    component-wise lower comparisons from accepting mixed or stronger work.

    Arguments:
        hasher: Configured Scrypt hasher.
        decoded: Parsed stored encoding metadata.

    Returns:
        Whether the complete accepted Scrypt profile is current.
    """
    if not cast("str", decoded["salt"]) or not _has_valid_base64(
        cast("str", decoded["hash"]),
        64,
    ):
        return False

    return (
        decoded["work_factor"],
        decoded["block_size"],
        decoded["parallelism"],
    ) == (
        hasher.work_factor,
        hasher.block_size,
        hasher.parallelism,
    )


@cache
def dummy_password_hashes() -> tuple[str, ...]:
    """Build one current-cost dummy hash for every configured algorithm.

    Defers memory-hard work until a process handles its first login request, preventing parallel
    test collection and idle application workers from allocating every hasher simultaneously.

    Arguments:
        None.

    Returns:
        Encoded dummy passwords in configured hasher order.
    """
    return tuple(
        make_password(secrets.token_urlsafe(32), hasher=cast("str", hasher.algorithm))
        for hasher in get_hashers()
    )


def verify_encoded_password(
    password: str,
    encoded: str,
    *,
    preferred: str = "default",
) -> bool:
    """Verify one password without permitting an account upgrade.

    Delegates to Django's encoded-password verifier without a setter so rejection paths can measure
    a stored hash while preserving database state until active-account acceptance is established.

    Arguments:
        password: Submitted raw password.
        encoded: Stored or schedule-only encoded password.
        preferred: Hasher whose current work parameters define runtime hardening.

    Returns:
        Whether the password matches the encoded value.
    """
    return check_password(password, encoded, preferred=preferred)


def classify_password_hash(encoded: str) -> PasswordHashDisposition:
    """Classify one stored encoding against its hasher's explicit accepted-profile policy.

    Parses only bounded database metadata before deciding whether verification may run. Exact
    current Argon2 and Scrypt profiles are accepted, while PBKDF2 additionally accepts positive
    lower iteration counts that Django can harden to current equivalent cost.

    Arguments:
        encoded: Stored password encoding.

    Returns:
        Current, recognized-lower, or reset-required disposition.
    """
    disposition = PasswordHashDisposition.RESET_REQUIRED
    if len(encoded) <= MAX_ENCODED_PASSWORD_LENGTH:
        try:
            hasher = identify_hasher(encoded)
            decoded = hasher.decode(encoded)
        except AssertionError, TypeError, ValueError:
            pass
        else:
            profile_policy = settings.PASSWORD_HASH_ACCEPTED_PROFILES.get(
                cast("str", hasher.algorithm)
            )
            if profile_policy == "current-or-lower-iterations" and isinstance(
                hasher,
                (PBKDF2PasswordHasher, PBKDF2SHA1PasswordHasher),
            ):
                disposition = _classify_pbkdf2_profile(hasher, decoded)
            elif (
                profile_policy == "current-exact"
                and isinstance(hasher, Argon2PasswordHasher)
                and _argon2_profile_is_current(hasher, decoded, encoded)
            ) or (
                profile_policy == "current-exact"
                and isinstance(hasher, ScryptPasswordHasher)
                and _scrypt_profile_is_current(hasher, decoded)
            ):
                disposition = PasswordHashDisposition.CURRENT

    return disposition


def account_throttle_identity(username: str) -> str:
    """Resolve one account bucket using authoritative PostgreSQL semantics.

    Always asks the primary for both database lowercase normalization and an existing account,
    selecting an immutable ID when found and a consistently normalized fallback otherwise.

    Arguments:
        username: Submitted account identifier.

    Returns:
        Opaque hash for an existing account ID or unknown normalized identifier.

    Raises:
        DatabaseError: If the authoritative primary cannot resolve identity.
    """
    with connections["default"].cursor() as cursor:
        cursor.execute("SELECT LOWER(%s)", [username])
        normalized = cast("tuple[str]", cursor.fetchone())[0]

    account = resolve_login_account(username)
    identity = f"account:{account.pk}" if account is not None else f"unknown:{normalized}"

    return sha256(identity.encode()).hexdigest()


def resolve_login_account(username: str) -> User | None:
    """Resolve one username with the model constraint's PostgreSQL semantics.

    Compares ``LOWER(username)`` with ``LOWER(input)`` on the primary database, matching the
    functional uniqueness constraint without Unicode behavior from another lookup transform.

    Arguments:
        username: Submitted account username.

    Returns:
        The uniquely matching account, or None when no account matches.

    Raises:
        MultipleObjectsReturned: If authoritative data violates the uniqueness constraint.
        DatabaseError: If the primary cannot resolve the account.
    """
    try:
        return (
            User.objects.using("default")
            .alias(login_username=Lower("username"))
            .get(login_username=Lower(Value(username)))
        )
    except User.DoesNotExist:
        return None


def verify_login_credentials(username: str, password: str) -> AuthenticatedCredentialSnapshot:
    """Verify token-login credentials without revealing account state.

    Performs the complete bounded hash schedule without a row lock. Accepted profiles replace
    their matching dummy, reset-required profiles run only the unknown-account schedule, and a
    successful active match returns immutable state for issuance-time locked revalidation.

    Arguments:
        username: Submitted account username.
        password: Submitted raw password.

    Returns:
        Immutable authenticated account and password-security snapshot.

    Raises:
        AuthenticationFailed: If the account is unknown, inactive, or has another password.
    """
    account = resolve_login_account(username)
    original_encoded = account.password if account is not None else None
    disposition = (
        classify_password_hash(original_encoded)
        if original_encoded is not None
        else PasswordHashDisposition.RESET_REQUIRED
    )
    account_hasher = (
        identify_hasher(original_encoded)
        if original_encoded is not None
        and disposition is not PasswordHashDisposition.RESET_REQUIRED
        else None
    )
    account_algorithm = account_hasher.algorithm if account_hasher is not None else None
    account_requires_hasher_update = (
        account_hasher.must_update(original_encoded)
        if account_hasher is not None and original_encoded is not None
        else False
    )
    preferred_algorithm = get_hashers()[0].algorithm
    account_requires_upgrade = account_requires_hasher_update or (
        account_algorithm is not None and account_algorithm != preferred_algorithm
    )
    account_is_active = account is not None and account.is_active

    password_matches = False
    for dummy_hash in dummy_password_hashes():
        dummy_algorithm = cast("str", identify_hasher(dummy_hash).algorithm)
        candidate_hash = (
            original_encoded
            if original_encoded is not None and account_algorithm == dummy_algorithm
            else dummy_hash
        )
        candidate_matches = verify_encoded_password(
            password,
            candidate_hash,
            preferred=dummy_algorithm,
        )
        if candidate_hash is original_encoded:
            password_matches = candidate_matches
            if (
                candidate_matches
                and not account_is_active
                and disposition is PasswordHashDisposition.RECOGNIZED_LOWER
                and account_hasher is not None
            ):
                account_hasher.harden_runtime(
                    password,
                    original_encoded,
                )

    if account is None or not password_matches or not account.is_active:
        raise AuthenticationFailed

    replacement_encoded_password = make_password(password) if account_requires_upgrade else None

    return AuthenticatedCredentialSnapshot(
        account_id=account.pk,
        encoded_password=account.password,
        is_active=account.is_active,
        replacement_encoded_password=replacement_encoded_password,
    )


def lock_authenticated_account(snapshot: AuthenticatedCredentialSnapshot) -> User:
    """Lock and revalidate account state immediately before credential issuance.

    Resolves the authenticated identity on the primary, compares active and password state exactly
    with the lock-free snapshot, and persists only a precomputed preferred encoding before callers
    issue credentials in the same transaction.

    Arguments:
        snapshot: Immutable state returned by complete credential verification.

    Returns:
        Locked active account whose security state still matches.

    Raises:
        AuthenticationFailed: If the account disappeared or its security state changed.
        TransactionManagementError: If the caller has not opened a primary transaction.
    """
    try:
        account = User.objects.using("default").select_for_update().get(pk=snapshot.account_id)
    except User.DoesNotExist as error:
        raise AuthenticationFailed from error

    if (
        account.is_active != snapshot.is_active
        or not account.is_active
        or account.password != snapshot.encoded_password
    ):
        raise AuthenticationFailed

    if snapshot.replacement_encoded_password is not None:
        account.password = snapshot.replacement_encoded_password
        account.save(using="default", update_fields=["password"])

    return account
