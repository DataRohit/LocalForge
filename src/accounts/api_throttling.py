"""Shared cache-backed API throttle scopes.

Enforces reusable anonymous, authentication, recovery, and authenticated-read limits through the
configured shared cache while leaving authoritative credential admissions in PostgreSQL.
"""

from __future__ import annotations

import hmac
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from typing import TYPE_CHECKING, cast, override

from django.conf import settings
from django.core.cache import caches
from rest_framework.throttling import BaseThrottle
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.settings import api_settings
from rest_framework_simplejwt.tokens import Token, UntypedToken

from accounts.request_throttling import parse_throttle_rate, trusted_client_address

if TYPE_CHECKING:
    from rest_framework.request import Request
    from rest_framework.views import APIView

    from config.cache import ResilientRedisCache

TEST_SERVER_TIME_MILLISECONDS: int | None = None
UNSAFE_METHODS = frozenset({"DELETE", "PATCH", "POST", "PUT"})
SAFE_READ_METHODS = frozenset({"GET", "HEAD"})


@dataclass(frozen=True, slots=True)
class CacheThrottleDecision:
    """Represent one shared-cache admission outcome.

    Stores whether every requested dimension admitted and the whole-second delay returned by
    Valkey when one dimension was already exhausted.

    Attributes:
        admitted: Whether all dimensions were incremented atomically.
        retry_after_seconds: Delay until the current fixed window ends.

    Members:
        None.
    """

    admitted: bool
    retry_after_seconds: int


def _opaque_identity(scope: str, kind: str, value: str) -> str:
    """Build one opaque cache identity.

    Authenticates a domain-separated scope, dimension kind, and canonical value before client or
    account material enters cache keys, preventing offline reproduction from known identifiers.

    Arguments:
        scope: Stable throttle policy scope.
        kind: Dimension label separating address and account namespaces.
        value: Canonical identity value to protect.

    Returns:
        Opaque dimension key.
    """
    message = f"localforge/api-throttle/v1\0{scope}\0{kind}\0{value}".encode()
    key = settings.API_THROTTLE_IDENTITY_HMAC_KEY
    digest = hmac.new(key, message, sha256).hexdigest()

    return f"{kind}:{digest}"


def _account_cluster_hash_tag(scope: str, account_identity: str) -> str:
    """Build one opaque account-scoped Valkey Cluster hash tag.

    Keeps the readable policy scope while authenticating immutable account material under a
    cluster-tag dimension, so atomic dimensions co-locate and unrelated accounts distribute.

    Arguments:
        scope: Stable cache namespace for the reusable policy.
        account_identity: Immutable account identity selected for admission.

    Returns:
        Readable scope and opaque digest suitable for braces in a Valkey key.
    """
    digest = _opaque_identity(scope, "cluster-tag", account_identity).removeprefix("cluster-tag:")

    return f"{scope}:{digest}"


def _signed_token_subject(encoded: str) -> str | None:
    """Extract one cryptographically validated token subject.

    Validates signature, temporal claims, and token structure before exposing only the configured
    immutable subject, preventing unsigned or malformed body values from creating account buckets.

    Arguments:
        encoded: Body-carried token or refresh credential.

    Returns:
        Stable account identity when the credential validates, otherwise ``None``.
    """
    try:
        token = UntypedToken(cast("Token", encoded))
    except OSError, OverflowError, TokenError, TypeError, ValueError:
        return None

    account = token.payload.get(api_settings.USER_ID_CLAIM)

    return f"id:{account}" if isinstance(account, str) and account else None


def _request_account_identity(request: Request, view: APIView) -> str | None:
    """Resolve an account dimension available before the view runs.

    Prefers the authenticated immutable identifier, then permits only the signed subject from the
    body token field explicitly declared by the selected view. Raw selectors never create buckets.

    Arguments:
        request: REST request entering throttling.
        view: Selected API view declaring any signed body-token field.

    Returns:
        Stable request account identity when one is available, otherwise ``None``.
    """
    user = request.user
    if user.is_authenticated:
        return f"id:{user.pk}"

    field = getattr(view, "signed_subject_throttle_field", None)
    if field not in {"refresh", "token"}:
        return None

    data = request.data
    if not isinstance(data, Mapping):
        return None

    encoded = data.get(field)

    return _signed_token_subject(encoded) if isinstance(encoded, str) and encoded else None


def _cache_decision(
    scope: str,
    rate: str,
    identities: tuple[str, ...],
    *,
    account_identity: str | None = None,
) -> CacheThrottleDecision:
    """Make one atomic fixed-window decision for all supplied dimensions.

    Authenticates each logical identity, delegates time and all-or-nothing mutation to one Valkey
    script, and applies the accepted fail-open policy only when the evictable cache is unavailable.

    Arguments:
        scope: Stable cache namespace for the reusable policy.
        rate: Environment-derived count-per-period rate.
        identities: Distinct logical dimension values checked as one admission.
        account_identity: Immutable account identity shared by a multi-key admission.

    Returns:
        Shared-cache admission outcome and retry delay.

    Raises:
        ValueError: If the configured rate is invalid.
    """
    limit, window_seconds = parse_throttle_rate(rate)
    namespace = (
        f"api-throttle:{{{_account_cluster_hash_tag(scope, account_identity)}}}"
        if account_identity is not None
        else f"api-throttle:{scope}"
    )
    keys = tuple(
        f"{namespace}:{_opaque_identity(scope, kind, value)}"
        for kind, value in (identity.split(":", maxsplit=1) for identity in identities)
    )
    cache = cast("ResilientRedisCache", caches["default"])
    raw = cache.atomic_fixed_window_admit(
        keys,
        limit=limit,
        window_seconds=window_seconds,
        now_milliseconds=TEST_SERVER_TIME_MILLISECONDS,
    )
    if raw is None:
        return CacheThrottleDecision(admitted=True, retry_after_seconds=0)

    admitted, retry_after_seconds = raw

    return CacheThrottleDecision(
        admitted=admitted,
        retry_after_seconds=retry_after_seconds,
    )


def boundary_address_admission(address: str) -> CacheThrottleDecision:
    """Admit one API request at the outer ASGI source boundary.

    Charges a deliberately broad address-only scope before body rejection, preflight, content
    negotiation, parsing, authentication, permissions, and DRF throttling. Identified operations
    later add only account and account-address composite dimensions in their tighter scope.

    Arguments:
        address: Canonical source address resolved at the ASGI trust boundary.

    Returns:
        Broad source admission outcome.
    """
    return _cache_decision(
        "boundary-address",
        cast("str", settings.API_BOUNDARY_ADDRESS_THROTTLE_RATE),
        (f"address:{address}",),
    )


class CacheScopeThrottle(BaseThrottle):
    """Enforce one fixed-window scope through the shared cache.

    Inherits from DRF's ``BaseThrottle`` and increments atomic address and available account
    counters, making the limit common to every application process using the configured cache.

    Attributes:
        rate_setting: Django setting carrying the environment-derived scope rate.
        scope: Stable cache namespace for the reusable policy.
        retry_after_seconds: Delay until the active fixed window ends.

    Members:
        allow_request: Admit or reject every applicable dimension.
        applies: Decide whether this scope governs a request.
        wait: Return the complete fixed-window retry delay.
    """

    rate_setting = ""
    scope = ""
    retry_after_seconds: int | None = None

    def applies(self, request: Request, view: APIView) -> bool:
        """Decide whether this scope governs one request.

        Leaves the applicability rule to concrete scope classes so one shared counter mechanism
        serves anonymous use, authentication operations, and authenticated reads.

        Arguments:
            request: REST request entering throttling.
            view: View selected for the request.

        Returns:
            Whether counters should be charged.
        """
        del request, view

        return True

    @override
    def allow_request(self, request: Request, view: APIView) -> bool:
        """Atomically increment every applicable fixed-window dimension.

        Selects address-only identity for unidentified requests or account and address-account
        composite identities for identified requests, then delegates one all-or-nothing decision to
        Valkey and fails open only when the evictable cache is absent.

        Arguments:
            request: REST request entering throttling.
            view: View selected for the request.

        Returns:
            Whether every available dimension remains within the configured limit.

        Raises:
            ValueError: If the environment-derived throttle rate is invalid.
        """
        if not self.applies(request, view):
            return True

        address = trusted_client_address(request)
        account_identity = _request_account_identity(request, view)
        identities = (
            (f"address:{address}",)
            if account_identity is None
            else (
                f"account:{account_identity}",
                f"composite:{address}\0{account_identity}",
            )
        )
        decision = _cache_decision(
            self.scope,
            cast("str", getattr(settings, self.rate_setting)),
            identities,
            account_identity=account_identity,
        )
        self.retry_after_seconds = decision.retry_after_seconds if not decision.admitted else None

        return decision.admitted

    @override
    def wait(self) -> float | None:
        """Return the active fixed-window delay.

        Supplies DRF's ``Retry-After`` header after rejection and no estimate before an applicable
        counter has exceeded its environment-derived threshold.

        Arguments:
            None.

        Returns:
            Whole-second retry delay, or ``None`` before rejection.
        """
        return float(self.retry_after_seconds) if self.retry_after_seconds is not None else None


class AnonymousApiThrottle(CacheScopeThrottle):
    """Limit anonymous use across the fixed API surface.

    Inherits from ``CacheScopeThrottle`` and applies only while DRF has no authenticated account,
    leaving authenticated operations to their explicit route scopes.

    Attributes:
        rate_setting: Anonymous rate loaded from the environment.
        scope: Anonymous cache namespace.

    Members:
        applies: Select unauthenticated requests.
    """

    rate_setting = "API_ANONYMOUS_THROTTLE_RATE"
    scope = "anonymous"

    @override
    def applies(self, request: Request, view: APIView) -> bool:
        """Select only requests without an authenticated account.

        Uses DRF's resolved user state so valid token and JSON web credentials are never charged to
        the broad anonymous scope.

        Arguments:
            request: REST request entering throttling.
            view: View selected for the request.

        Returns:
            Whether the caller is anonymous.
        """
        return (
            not request.user.is_authenticated and _request_account_identity(request, view) is None
        )


class AuthenticationRecoveryThrottle(CacheScopeThrottle):
    """Limit authentication, recovery, and account-security operations.

    Inherits from ``CacheScopeThrottle`` and provides one tight aggregate scope in addition to
    each endpoint's accepted PostgreSQL-backed security admission where that boundary exists.

    Attributes:
        rate_setting: Authentication and recovery rate loaded from the environment.
        scope: Authentication cache namespace.

    Members:
        applies: Select unsafe authentication and recovery operations.
    """

    rate_setting = "API_AUTHENTICATION_THROTTLE_RATE"
    scope = "authentication"

    @override
    def applies(self, request: Request, view: APIView) -> bool:
        """Select only unsafe authentication and recovery methods.

        Excludes safe HEAD and metadata OPTIONS requests from tight account budgets while leaving
        broad pre-framework source admission to the API boundary.

        Arguments:
            request: REST request entering throttling.
            view: View selected for the request.

        Returns:
            Whether the method can perform an authentication or account-security write.
        """
        del view

        return cast("str", request.method) in UNSAFE_METHODS


class AuthenticatedReadThrottle(CacheScopeThrottle):
    """Limit authenticated reads by both address and immutable account.

    Inherits from ``CacheScopeThrottle`` and applies only to safe GET requests with a resolved
    account, allowing address and account to be constrained independently across workers.

    Attributes:
        rate_setting: Authenticated-read rate loaded from the environment.
        scope: Authenticated-read cache namespace.

    Members:
        applies: Select authenticated GET requests.
    """

    rate_setting = "API_AUTHENTICATED_READ_THROTTLE_RATE"
    scope = "authenticated-read"

    @override
    def applies(self, request: Request, view: APIView) -> bool:
        """Select authenticated GET requests only.

        Keeps profile mutations outside the looser read budget while charging both account and
        address dimensions for the one current authenticated read route.

        Arguments:
            request: REST request entering throttling.
            view: View selected for the request.

        Returns:
            Whether the request is an authenticated GET.
        """
        del view

        method = cast("str", request.method)

        return method in SAFE_READ_METHODS and bool(request.user.is_authenticated)


class AuthenticationRecoveryWriteThrottle(AuthenticationRecoveryThrottle):
    """Limit only unsafe account-security methods at a mixed-method route.

    Inherits the tight authentication scope and skips GET so the profile route can use its looser
    authenticated-read policy without the tighter mutation budget masking it.

    Attributes:
        None beyond those inherited from ``AuthenticationRecoveryThrottle``.

    Members:
        applies: Select every non-GET request.
    """

    @override
    def applies(self, request: Request, view: APIView) -> bool:
        """Select non-GET requests.

        Separates the fixed profile route's read and mutation budgets while retaining one shared
        authentication cache namespace for every account-security operation.

        Arguments:
            request: REST request entering throttling.
            view: View selected for the request.

        Returns:
            Whether the request method is not GET.
        """
        del view

        return cast("str", request.method) in UNSAFE_METHODS
