"""Unit policy checks for reusable API throttling.

Verifies public route opt-outs and the load-balancer exemption are explicit in runtime docstrings,
so deny-by-default permissions cannot be weakened without recording why.
"""

import base64
import hmac
import logging
from hashlib import sha256
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID

import pytest
import redis.cluster
from django.test import override_settings
from rest_framework.permissions import AllowAny

from accounts.api_throttling import (
    AnonymousApiThrottle,
    AuthenticationRecoveryThrottle,
    CacheScopeThrottle,
    boundary_address_admission,
)
from accounts.jwt_authentication import JWTCreateView, JWTRefreshView, JWTVerifyView
from accounts.password_management import PasswordResetConfirmView, PasswordResetRequestView
from accounts.token_authentication import TokenLoginView
from accounts.user_profiles import ActivationResendView, UserRegistrationView
from accounts.username_management import UsernameResetConfirmView, UsernameResetRequestView
from config.health import ReadinessView

PUBLIC_ACCOUNT_VIEWS = (
    UserRegistrationView,
    ActivationResendView,
    PasswordResetRequestView,
    PasswordResetConfirmView,
    UsernameResetRequestView,
    UsernameResetConfirmView,
    TokenLoginView,
    JWTCreateView,
    JWTRefreshView,
    JWTVerifyView,
)
IDENTITY_HMAC_KEY = bytes(range(64))
IDENTITY_HMAC_KEY_TEXT = base64.urlsafe_b64encode(IDENTITY_HMAC_KEY).decode().rstrip("=")


def expected_identity(scope: str, kind: str, value: str) -> str:
    """Calculate one specified throttle identity independently.

    Uses the published domain and field order directly so a production key can be compared with a
    known contract rather than reconstructed through the implementation helper.

    Arguments:
        scope: Stable throttle policy scope.
        kind: Dimension kind within that scope.
        value: Canonical dimension value.

    Returns:
        Lowercase hexadecimal HMAC-SHA-256 digest.
    """
    message = f"localforge/api-throttle/v1\0{scope}\0{kind}\0{value}".encode()

    return hmac.new(IDENTITY_HMAC_KEY, message, sha256).hexdigest()


class RecordingThrottleCache:
    """Capture logical keys submitted for one atomic cache admission.

    Inherits nothing and implements the shared cache throttle seam so tests can inspect cluster
    routing without requiring a Valkey Cluster deployment.

    Attributes:
        calls: Ordered multi-key admissions observed.

    Members:
        atomic_fixed_window_admit: Record one admitted decision.
    """

    def __init__(self) -> None:
        """Initialize an empty admission history.

        Creates one process-local list so each test can compare submitted key groups in order.
        No cache or network client is constructed.

        Arguments:
            None.

        Returns:
            None.
        """
        self.calls: list[tuple[str, ...]] = []

    def atomic_fixed_window_admit(
        self,
        keys: tuple[str, ...],
        *,
        limit: int,
        window_seconds: int,
        now_milliseconds: int | None = None,
    ) -> tuple[bool, int]:
        """Record and admit one atomic decision.

        Preserves the complete logical key tuple while returning the successful adapter result the
        public throttle expects.

        Arguments:
            keys: Logical throttle keys submitted together.
            limit: Configured fixed-window count.
            window_seconds: Configured fixed-window duration.
            now_milliseconds: Optional coordinated test time.

        Returns:
            Admitted decision with no retry delay.
        """
        del limit, window_seconds, now_milliseconds
        self.calls.append(keys)

        return True, 0


@pytest.mark.unit
def test_address_identity_is_stable_keyed_and_separated_by_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Protect one canonical address independently in every policy scope.

    Admits the same address twice through the anonymous scope and once through the outer boundary,
    proving stability, exact domain separation, and resistance to the previous unkeyed digests.

    Arguments:
        monkeypatch: Fixture replacing the external cache adapter.

    Returns:
        None.

    Raises:
        AssertionError: If identities drift, cross scopes, or reproduce an unkeyed digest.
    """
    cache = RecordingThrottleCache()
    address = "198.51.100.214"
    request = SimpleNamespace(
        user=SimpleNamespace(is_authenticated=False),
        method="POST",
        META={"REMOTE_ADDR": address},
    )
    monkeypatch.setattr("accounts.api_throttling.caches", {"default": cache})

    with override_settings(
        API_THROTTLE_IDENTITY_HMAC_KEY=IDENTITY_HMAC_KEY,
        API_BOUNDARY_ADDRESS_THROTTLE_RATE="3/minute",
        API_ANONYMOUS_THROTTLE_RATE="3/minute",
    ):
        boundary_address_admission(address)
        for _attempt in range(2):
            assert AnonymousApiThrottle().allow_request(
                cast("Any", request),
                cast("Any", object()),
            )

    boundary_key = cache.calls[0][0]
    first_anonymous_key = cache.calls[1][0]
    second_anonymous_key = cache.calls[2][0]
    assert boundary_key == (
        "api-throttle:boundary-address:address:"
        f"{expected_identity('boundary-address', 'address', address)}"
    )
    assert first_anonymous_key == (
        f"api-throttle:anonymous:address:{expected_identity('anonymous', 'address', address)}"
    )
    assert first_anonymous_key == second_anonymous_key
    assert boundary_key != first_anonymous_key
    unkeyed = {
        sha256(address.encode()).hexdigest(),
        sha256(f"boundary-address\0{address}".encode()).hexdigest(),
        sha256(f"anonymous\0{address}".encode()).hexdigest(),
    }
    rendered_keys = f"{boundary_key}\n{first_anonymous_key}"

    assert all(digest not in rendered_keys for digest in unkeyed)
    assert address not in boundary_key
    assert address not in first_anonymous_key


@pytest.mark.unit
def test_account_throttle_keys_share_cluster_slot_and_distribute_by_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Route one account's atomic dimensions together without exposing identity.

    Sends two authenticated admissions through the public throttle seam, proving each account and
    composite pair shares a hash slot while unrelated accounts use distinct tags and slots.

    Arguments:
        monkeypatch: Fixture replacing the external cache adapter.

    Returns:
        None.

    Raises:
        AssertionError: If an atomic pair crosses slots, identities leak, or accounts co-locate.
    """
    cache = RecordingThrottleCache()
    first_account = UUID("018f4f10-7b6a-7c80-8a5f-111111111111")
    second_account = UUID("018f4f10-7b6a-7c80-8a5f-222222222222")
    accounts = (first_account, second_account)
    dimensions = ("account", "composite")
    address = "198.51.100.214"
    monkeypatch.setattr("accounts.api_throttling.caches", {"default": cache})

    with override_settings(
        API_THROTTLE_IDENTITY_HMAC_KEY=IDENTITY_HMAC_KEY,
        API_AUTHENTICATION_THROTTLE_RATE="3/minute",
    ):
        for account in accounts:
            request = SimpleNamespace(
                user=SimpleNamespace(is_authenticated=True, pk=account),
                method="POST",
                META={"REMOTE_ADDR": address},
            )
            assert AuthenticationRecoveryThrottle().allow_request(
                cast("Any", request),
                cast("Any", object()),
            )

    assert len(cache.calls) == len(accounts)
    first_keys, second_keys = cache.calls
    cluster = cast("Any", redis.cluster)
    for keys in cache.calls:
        assert len(keys) == len(dimensions)
        assert f":{dimensions[0]}:" in keys[0]
        assert f":{dimensions[1]}:" in keys[1]
        assert keys[0].index("}") < keys[0].index(":account:")
        assert keys[1].index("}") < keys[1].index(":composite:")
        assert cluster.key_slot(keys[0].encode()) == cluster.key_slot(keys[1].encode())
        assert address not in "".join(keys)

    assert str(first_account) not in "".join(first_keys)
    assert str(second_account) not in "".join(second_keys)
    first_identity = f"id:{first_account}"
    first_tag = expected_identity("authentication", "cluster-tag", first_identity)
    first_account_digest = expected_identity("authentication", "account", first_identity)
    first_composite_digest = expected_identity(
        "authentication",
        "composite",
        f"{address}\0{first_identity}",
    )
    assert first_keys == (
        f"api-throttle:{{authentication:{first_tag}}}:account:{first_account_digest}",
        f"api-throttle:{{authentication:{first_tag}}}:composite:{first_composite_digest}",
    )
    assert first_tag != first_account_digest
    old_unkeyed = {
        sha256(first_identity.encode()).hexdigest(),
        sha256(f"authentication\0{first_identity}".encode()).hexdigest(),
        sha256(f"{address}\0{first_identity}".encode()).hexdigest(),
    }
    assert all(digest not in "\n".join(first_keys) for digest in old_unkeyed)
    assert cluster.key_slot(first_keys[0].encode()) != cluster.key_slot(second_keys[0].encode())


@pytest.mark.unit
def test_logged_throttle_keys_never_reveal_identity_or_hmac_material(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Keep cache-key diagnostics opaque even when a complete key is logged.

    Records one authenticated admission through a cache double that emits submitted keys, proving
    addresses, accounts, and the dedicated HMAC secret cannot reach diagnostic output.

    Arguments:
        monkeypatch: Fixture replacing the external cache adapter.
        caplog: Fixture capturing diagnostic output.

    Returns:
        None.

    Raises:
        AssertionError: If protected identity or key material reaches a log record.
    """
    cache = RecordingThrottleCache()
    account = UUID("018f4f10-7b6a-7c80-8a5f-333333333333")
    address = "203.0.113.27"
    request = SimpleNamespace(
        user=SimpleNamespace(is_authenticated=True, pk=account),
        method="POST",
        META={"REMOTE_ADDR": address},
    )
    monkeypatch.setattr("accounts.api_throttling.caches", {"default": cache})

    with (
        override_settings(
            API_THROTTLE_IDENTITY_HMAC_KEY=IDENTITY_HMAC_KEY,
            API_AUTHENTICATION_THROTTLE_RATE="3/minute",
        ),
        caplog.at_level(logging.INFO, logger=__name__),
    ):
        assert AuthenticationRecoveryThrottle().allow_request(
            cast("Any", request),
            cast("Any", object()),
        )
        logging.getLogger(__name__).info("throttle keys: %s", cache.calls[-1])

    assert "api-throttle" in caplog.text
    assert address not in caplog.text
    assert str(account) not in caplog.text
    assert IDENTITY_HMAC_KEY_TEXT not in caplog.text


@pytest.mark.unit
def test_base_cache_scope_applies_without_an_additional_filter() -> None:
    """Keep the reusable cache scope applicable by default.

    Calls the public throttle applicability seam with opaque request and view values so subclasses
    can inherit unconditional behavior without depending on either object's implementation.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the reusable base unexpectedly filters requests.
    """
    throttle = CacheScopeThrottle()

    assert throttle.applies(cast("Any", object()), cast("Any", object())) is True


@pytest.mark.unit
@pytest.mark.parametrize("view_type", PUBLIC_ACCOUNT_VIEWS)
def test_every_public_account_view_documents_its_permission_opt_out(view_type: type) -> None:
    """Require an explicit runtime reason for every public account route.

    Inspects the fixed public view set and verifies each route deliberately replaces central
    authentication and permission defaults rather than becoming public by omission.

    Arguments:
        view_type: Public API view class under policy inspection.

    Returns:
        None.

    Raises:
        AssertionError: If a public route omits its explicit opt-out or reason.
    """
    public_view = cast("Any", view_type)

    assert public_view.authentication_classes == ()
    assert tuple(public_view.permission_classes) == (AllowAny,)
    assert "Public access" in str(public_view.__doc__)


@pytest.mark.unit
def test_readiness_documents_why_load_balancer_probes_are_unthrottled() -> None:
    """Keep the existing health route public and unthrottled for the load balancer.

    Verifies the dynamic readiness view states its operational exception while retaining explicit
    public permission and throttle opt-outs.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If health probes inherit account throttling or lose their rationale.
    """
    readiness_view = cast("Any", ReadinessView)

    assert tuple(readiness_view.permission_classes) == (AllowAny,)
    assert readiness_view.throttle_classes == []
    assert "load-balancer probes remain unthrottled" in str(readiness_view.__doc__)
