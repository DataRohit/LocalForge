"""Token authentication HTTP endpoints.

Exchanges active-account credentials for DRF tokens and destroys those credentials on logout,
keeping all authentication failures indistinguishable while exposing only the fixed versioned
routes owned by Ticket 29.
"""

# mypy: disable-error-code=misc

from __future__ import annotations

import secrets
from base64 import b64decode
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from functools import cache
from hashlib import sha256
from http import HTTPStatus
from typing import TYPE_CHECKING, Any, cast, override

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
from django.db import DatabaseError, connections, transaction
from django.db.models import Value
from django.db.models.functions import Lower
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema
from rest_framework.authtoken.models import Token
from rest_framework.exceptions import AuthenticationFailed, ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.serializers import CharField, DictField, Serializer
from rest_framework.throttling import BaseThrottle
from rest_framework.views import APIView

from accounts.api_throttling import (
    AnonymousApiThrottle,
    AuthenticationRecoveryThrottle,
)
from accounts.authentication import PrimaryTokenAuthentication
from accounts.login_throttle import PostgresLoginThrottleStore, RollingWindowRule
from accounts.models import User
from accounts.request_throttling import parse_throttle_rate, trusted_client_address
from config.api_errors import (
    AUTHENTICATION_FAILED,
    INTERNAL_SERVER_ERROR,
    METHOD_NOT_ALLOWED,
    NOT_ACCEPTABLE,
    NOT_AUTHENTICATED,
    PARSE_ERROR,
    REQUEST_TOO_LARGE,
    SERVICE_UNAVAILABLE,
    THROTTLED,
    UNSUPPORTED_MEDIA_TYPE,
    VALIDATION_ERROR,
    ErrorDefinition,
    ServiceUnavailable,
)
from config.logs import REQUEST_ID_META_KEY

if TYPE_CHECKING:
    from uuid import UUID

    from argon2 import Parameters
    from rest_framework.request import Request

SCHEMA_REQUEST_ID = "00000000-0000-4000-8000-000000000000"
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


class TokenResponseSerializer(Serializer):
    """Describe the successful token-login body.

    Inherits from DRF's ``Serializer`` and exposes only the credential clients present in the
    authorization header, preventing account data from becoming part of the login contract.

    Attributes:
        token: DRF token value issued to the account.

    Members:
        None beyond those inherited from ``Serializer``.
    """

    token = CharField(read_only=True)


class ErrorEnvelopeSerializer(Serializer):
    """Describe the shared correlated API error envelope.

    Inherits from DRF's ``Serializer`` and mirrors the stable fields produced by the central
    exception handler while allowing validation details to retain their field-keyed structure.

    Attributes:
        code: Stable machine-readable error code.
        message: Safe human-readable message.
        details: Per-field validation details or an empty mapping.
        request_id: Correlation identifier shared with the response header.

    Members:
        None beyond those inherited from ``Serializer``.
    """

    code = CharField(read_only=True)
    message = CharField(read_only=True)
    details = DictField(read_only=True)
    request_id = CharField(read_only=True)


def error_example(
    name: str,
    definition: ErrorDefinition,
    *,
    details: dict[str, object] | None = None,
) -> OpenApiExample:
    """Build one schema example from the central error vocabulary.

    Reuses the same code and message definitions as runtime responses, preventing documentation
    text from drifting while allowing a validation response to show its field detail.

    Arguments:
        name: Human-readable example name.
        definition: Central code and safe message pair.
        details: Optional field validation mapping.

    Returns:
        Response-only OpenAPI example.
    """
    return OpenApiExample(
        name,
        value={
            "code": definition.code.value,
            "message": definition.message,
            "details": details or {},
            "request_id": SCHEMA_REQUEST_ID,
        },
        response_only=True,
    )


def error_response(
    description: str,
    name: str,
    definition: ErrorDefinition,
) -> OpenApiResponse:
    """Build one documented API error response.

    Couples the shared envelope serializer to a concrete response example so every listed status
    shows clients the exact correlated body rather than only a component reference.

    Arguments:
        description: Client-facing explanation of the failure condition.
        name: Human-readable example name.
        definition: Central code and safe message pair.

    Returns:
        OpenAPI response carrying the shared envelope and one example.
    """
    return OpenApiResponse(
        response=ErrorEnvelopeSerializer,
        description=description,
        examples=[error_example(name, definition)],
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


def normalized_login_username(data: object) -> str | None:
    """Normalize a raw username with the login serializer's public field contract.

    Runs only the declared username field so valid account identities are throttled even when
    another credential field is absent or invalid, while malformed bodies remain serializer-owned.

    Arguments:
        data: Parsed request body presented to the login throttle.

    Returns:
        The serializer-normalized username, or None when the body or field is invalid.
    """
    if not isinstance(data, Mapping):
        return None

    try:
        return cast(
            "str",
            TokenLoginSerializer().fields["username"].run_validation(data.get("username")),
        )
    except ValidationError:
        return None


class TokenLoginThrottle(BaseThrottle):
    """Enforce address and account login limits in one atomic decision.

    Inherits from DRF's ``BaseThrottle`` and delegates authoritative rolling-window state to the
    primary database beside account state, failing closed if PostgreSQL cannot decide.

    Attributes:
        retry_after_seconds: Server-derived delay after a rejected request.

    Members:
        allow_request: Atomically admit or reject both login dimensions.
        wait: Return the retry delay for DRF's response header.
    """

    retry_after_seconds: int | None = None

    @override
    def allow_request(self, request: Request, view: APIView) -> bool:
        """Atomically enforce every usable login dimension.

        Always limits the client address and adds an account rule only for non-empty string input,
        leaving non-object and invalid bodies to the serializer without unsafe mapping access.

        Arguments:
            request: Login request being admitted.
            view: Login view applying the throttle.

        Returns:
            Whether the authoritative shared database admitted the request.

        Raises:
            ServiceUnavailable: If authoritative shared throttle state is unavailable.
            ValueError: If a configured throttle rate is invalid.
        """
        del view
        address_limit, address_window = parse_throttle_rate(
            cast("str", settings.TOKEN_LOGIN_ADDRESS_THROTTLE_RATE)
        )
        address = sha256(trusted_client_address(request).encode()).hexdigest()
        rules = [
            RollingWindowRule(
                key=f"address:{address}",
                limit=address_limit,
                window_seconds=address_window,
            )
        ]
        try:
            username = normalized_login_username(request.data)

            if username is not None:
                account_limit, account_window = parse_throttle_rate(
                    cast("str", settings.TOKEN_LOGIN_ACCOUNT_THROTTLE_RATE)
                )
                rules.append(
                    RollingWindowRule(
                        key=f"account:{account_throttle_identity(username)}",
                        limit=account_limit,
                        window_seconds=account_window,
                    )
                )

            request_id = request.META.get(REQUEST_ID_META_KEY)
            correlation = request_id if isinstance(request_id, str) else "uncorrelated"
            member = f"{correlation}:{secrets.token_hex(16)}"
            decision = PostgresLoginThrottleStore(settings.LOGIN_THROTTLE_DATABASE_ALIAS).admit(
                tuple(rules), member=member
            )
        except DatabaseError as error:
            raise ServiceUnavailable from error

        self.retry_after_seconds = decision.retry_after_seconds

        return decision.admitted

    @override
    def wait(self) -> float | None:
        """Return the authoritative store's retry delay.

        Supplies DRF with the authoritative duration for ``Retry-After`` and returns no estimate
        before a denied decision has populated the value.

        Arguments:
            None.

        Returns:
            Retry delay in seconds, or None before a rejection.
        """
        return float(self.retry_after_seconds) if self.retry_after_seconds is not None else None


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


class TokenLoginSerializer(StrictRequestSerializer):
    """Validate the two token-login credential fields.

    Inherits from ``StrictRequestSerializer`` and accepts only username and password, leaving
    account lookup and password verification to the view's credential verifier.

    Attributes:
        username: Account username supplied by the client.
        password: Raw password supplied by the client and never returned.

    Members:
        None beyond those inherited from ``Serializer``.
    """

    username = CharField(max_length=150)
    password = CharField(trim_whitespace=False, write_only=True)


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


class TokenLoginView(APIView):
    """Issue the secondary non-expiring token credential.

    Inherits from DRF's ``APIView`` and opens anonymous credential exchange. Tokens are plaintext
    table keys with one non-expiring value per account; password profiles outside the accepted
    policy receive the generic bounded unknown-account rejection and require reset.

    Attributes:
        authentication_classes: Empty because callers do not yet hold a usable credential.
        permission_classes: Public access needed to exchange credentials.
        throttle_classes: Strict PostgreSQL address and account admission plus shared anonymous and
            authentication scopes.

    Members:
        get_authenticate_header: Preserve unauthorized status without authenticating the request.
        post: Validate credentials and return the account's token.
    """

    authentication_classes: tuple[type, ...] = ()
    permission_classes = (AllowAny,)
    throttle_classes = (
        TokenLoginThrottle,
        AnonymousApiThrottle,
        AuthenticationRecoveryThrottle,
    )

    @override
    def get_authenticate_header(self, request: Request) -> str:
        """Return the primary authentication challenge for credential failures.

        Keeps rejected username-and-password exchanges at status 401 while deliberately skipping
        request authentication, so a stale authorization header cannot block a client before
        Ticket 30 explicitly replaces the placeholder authenticator.

        Arguments:
            request: Login request whose body credentials were rejected.

        Returns:
            Stable Bearer challenge matching the globally primary scheme.

        """
        del request

        return "Bearer"

    @extend_schema(
        operation_id="token_login",
        summary="Exchange credentials for a token",
        description=(
            "Authenticates an active account without revealing whether a rejected username exists. "
            "Stored password profiles outside the accepted bounded policy receive the same generic "
            "failure and fixed dummy work as an unknown account and require password reset. "
            "The returned DRF token is a single non-expiring credential stored in plaintext as its "
            "table key and should be used only where the secondary scheme is appropriate."
        ),
        request=TokenLoginSerializer,
        auth=[],
        responses={
            HTTPStatus.OK: OpenApiResponse(
                response=TokenResponseSerializer,
                description="Credentials were accepted and the account token is returned.",
                examples=[
                    OpenApiExample(
                        "Token issued",
                        value={"token": "0123456789abcdef0123456789abcdef01234567"},
                        response_only=True,
                    )
                ],
            ),
            HTTPStatus.BAD_REQUEST: OpenApiResponse(
                response=ErrorEnvelopeSerializer,
                description="The request body is malformed or misses a required credential field.",
                examples=[
                    error_example("Malformed JSON", PARSE_ERROR),
                    error_example(
                        "Missing username",
                        VALIDATION_ERROR,
                        details={"username": ["This field is required."]},
                    ),
                ],
            ),
            HTTPStatus.UNAUTHORIZED: error_response(
                "Credentials were rejected without disclosing account state.",
                "Authentication failed",
                AUTHENTICATION_FAILED,
            ),
            HTTPStatus.METHOD_NOT_ALLOWED: error_response(
                "The token login endpoint accepts only POST.",
                "Method not allowed",
                METHOD_NOT_ALLOWED,
            ),
            HTTPStatus.NOT_ACCEPTABLE: error_response(
                "The requested response representation is unavailable.",
                "Not acceptable",
                NOT_ACCEPTABLE,
            ),
            HTTPStatus.CONTENT_TOO_LARGE: error_response(
                "The request body exceeds the environment-configured API limit.",
                "Request too large",
                REQUEST_TOO_LARGE,
            ),
            HTTPStatus.UNSUPPORTED_MEDIA_TYPE: error_response(
                "The submitted request representation is unsupported.",
                "Unsupported media type",
                UNSUPPORTED_MEDIA_TYPE,
            ),
            HTTPStatus.TOO_MANY_REQUESTS: error_response(
                "The client address or account identifier exceeded its configured login rate.",
                "Too many requests",
                THROTTLED,
            ),
            HTTPStatus.INTERNAL_SERVER_ERROR: error_response(
                "An unexpected server failure was contained.",
                "Internal server error",
                INTERNAL_SERVER_ERROR,
            ),
            HTTPStatus.SERVICE_UNAVAILABLE: error_response(
                "Authoritative account, login-admission, or token-persistence state is "
                "unavailable.",
                "Service unavailable",
                SERVICE_UNAVAILABLE,
            ),
        },
    )
    def post(self, request: Request) -> Response:
        """Validate credentials and return the account's token.

        Applies field validation before account authentication and creates the single DRF token only
        after an active account passes password verification.

        Arguments:
            request: REST request carrying username and password fields.

        Returns:
            Successful response containing only the token value.

        Raises:
            AuthenticationFailed: If the submitted credentials cannot authenticate.
            ServiceUnavailable: If authoritative account or token persistence is unavailable.
            ValidationError: If the submitted fields are invalid.
        """
        serializer = TokenLoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            snapshot = verify_login_credentials(
                cast("str", serializer.validated_data["username"]),
                cast("str", serializer.validated_data["password"]),
            )
            with transaction.atomic(using="default"):
                account = lock_authenticated_account(snapshot)
                token, _created = Token.objects.using("default").get_or_create(user=account)
        except DatabaseError as error:
            raise ServiceUnavailable from error

        return Response({"token": token.key}, status=HTTPStatus.OK)


class TokenLogoutView(APIView):
    """Destroy the secondary token presented by the caller.

    Inherits from DRF's ``APIView`` and accepts only DRF token authentication, ensuring logout
    revokes the exact credential that admitted the request rather than pretending a JSON web token
    was invalidated.

    Attributes:
        authentication_classes: Primary-reading token authentication for this operation.
        permission_classes: Authenticated callers only.
        throttle_classes: Shared authentication and account-security scope.

    Members:
        post: Delete the presented token.
    """

    authentication_classes = (PrimaryTokenAuthentication,)
    permission_classes = (IsAuthenticated,)
    throttle_classes = (AuthenticationRecoveryThrottle,)

    @extend_schema(
        operation_id="token_logout",
        summary="Destroy the caller's token",
        description=(
            "Deletes the DRF token that authenticated this request. The credential is stored in "
            "plaintext as the authentication table key and otherwise remains non-expiring until "
            "this operation revokes it."
        ),
        request=None,
        responses={
            HTTPStatus.NO_CONTENT: OpenApiResponse(
                response=None,
                description="The presented token was destroyed.",
            ),
            HTTPStatus.UNAUTHORIZED: OpenApiResponse(
                response=ErrorEnvelopeSerializer,
                description="A token was absent, malformed, unknown, or already revoked.",
                examples=[
                    error_example("Credential absent", NOT_AUTHENTICATED),
                    error_example("Authentication failed", AUTHENTICATION_FAILED),
                ],
            ),
            HTTPStatus.METHOD_NOT_ALLOWED: error_response(
                "The token logout endpoint accepts only POST.",
                "Method not allowed",
                METHOD_NOT_ALLOWED,
            ),
            HTTPStatus.NOT_ACCEPTABLE: error_response(
                "The requested response representation is unavailable.",
                "Not acceptable",
                NOT_ACCEPTABLE,
            ),
            HTTPStatus.CONTENT_TOO_LARGE: error_response(
                "The request body exceeds the environment-configured API limit.",
                "Request too large",
                REQUEST_TOO_LARGE,
            ),
            HTTPStatus.TOO_MANY_REQUESTS: error_response(
                "The client address or account exceeded the authentication scope.",
                "Too many requests",
                THROTTLED,
            ),
            HTTPStatus.INTERNAL_SERVER_ERROR: error_response(
                "An unexpected server failure was contained.",
                "Internal server error",
                INTERNAL_SERVER_ERROR,
            ),
        },
    )
    def post(self, request: Request) -> Response:
        """Delete the token that authenticated the request.

        Removes the plaintext table key before returning, so the same authorization header fails on
        the caller's next request.

        Arguments:
            request: Authenticated REST request carrying a DRF token.

        Returns:
            Empty successful response.

        Raises:
            DatabaseError: If token deletion cannot be persisted on the primary.
        """
        token = cast("Token", request.auth)
        token.delete()

        return Response(status=HTTPStatus.NO_CONTENT)
