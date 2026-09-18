"""Integration tests for password management.

Exercises the fixed password HTTP boundaries and captured-email seam against authoritative
account, credential, token, task, and admission state.
"""

from __future__ import annotations

import http.client
import json
import logging
import secrets
import statistics
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from http import HTTPStatus
from threading import Barrier, Event, Lock
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import parse_qs, urlparse

import pytest
from django.conf import settings
from django.contrib.auth import SESSION_KEY
from django.contrib.auth.password_validation import validate_password as django_validate_password
from django.core import mail
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import DatabaseError, close_old_connections, connections, transaction
from django.db.models import QuerySet
from django.test import Client as DjangoClient
from django.test import override_settings
from django.utils import timezone
from drf_spectacular.generators import SchemaGenerator
from freezegun import freeze_time
from rest_framework.authtoken.models import Token
from rest_framework_simplejwt.token_blacklist.models import OutstandingToken

import accounts.jwt_authentication as jwt_authentication_module
import accounts.password_management as password_management_module
import accounts.tasks as account_tasks_module
import accounts.token_authentication as token_authentication_module
from accounts.jwt_authentication import PrimaryRefreshToken
from accounts.login_throttle import PostgresLoginThrottleStore
from accounts.models import PasswordResetToken
from accounts.password_tokens import (
    create_password_reset_token,
    password_reset_token_is_expired,
)
from accounts.tasks import send_password_reset_email
from config.api_errors import ErrorCode, ServiceUnavailable
from config.logs import REQUEST_ID_HEADER, StructuredFormatter

if TYPE_CHECKING:
    from collections.abc import Callable

    from django.core.mail import EmailMultiAlternatives

    from accounts.models import User

CURRENT_PASSWORD = "Correct-Horse-Battery-Staple-33"  # noqa: S105
NEW_PASSWORD = "Different-Correct-Horse-Battery-34"  # noqa: S105
THIRD_PASSWORD = "Third-Correct-Horse-Battery-35"  # noqa: S105
SHORT_PASSWORD = "A1!"  # noqa: S105
RESET_ACCEPTED_MESSAGE = "If an account matches this address, a password reset email will be sent."
EXPECTED_RECOVERY_MESSAGE_COUNT = 2
EXPECTED_TASK_ARGUMENT_COUNT = 2
LOCKED_VALIDATION_CALL = 2
PREFLIGHT_EXPIRY_CALLS = 2
VIEW_LOOKUP_CALL = 2
TIMING_WARMUP_REQUESTS = 5
TIMING_MEASURED_REQUESTS = 30
TIMING_RELATIVE_LIMIT = 0.20
TIMING_ABSOLUTE_LIMIT_SECONDS = 0.010
PERSISTENCE_LOG_MINIMUM_RESPONSE_DURATION_SECONDS = 0.050
HIGH_RESET_RATE = "1000/hour"
LOW_RESET_RATE = "1/hour"
timing_logger = logging.getLogger("localforge.tests.password_reset_timing")
pytestmark = [
    pytest.mark.api_runtime,
    pytest.mark.xdist_group(name="password-management"),
]
SMTP_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
MAILPIT_TIMEOUT_SECONDS = 15
MAILPIT_POLL_SECONDS = 0.1
RACE_WAIT_SECONDS = 45


def _mailpit_request(method: str, path: str) -> object | None:
    """Call one testing Mailpit API path.

    Uses environment-specific host and web port settings so the same SMTP recovery test reaches
    Mailpit from host mode and from the one-off testing container.

    Arguments:
        method: HTTP method to send.
        path: Mailpit API path to request.

    Returns:
        Parsed JSON for a response body, otherwise None.

    Raises:
        AssertionError: If Mailpit rejects the request.
    """
    connection = http.client.HTTPConnection(
        settings.EMAIL_HOST,
        settings.MAILPIT_WEB_PORT,
        timeout=MAILPIT_TIMEOUT_SECONDS,
    )
    try:
        connection.request(method, path, headers={"Host": "localhost"})
        response = connection.getresponse()
        payload = response.read()
    finally:
        connection.close()

    assert response.status == HTTPStatus.OK
    if method == "DELETE" or not payload:
        return None
    return cast("object", json.loads(payload))


def _request_reset_link(
    client: DjangoClient,
    account: User,
    *,
    remote_address: str,
) -> dict[str, str]:
    """Request and extract one captured password-reset link.

    Uses only the public HTTP and email seams and returns the two query values a client frontend
    would submit to the confirmation route.

    Arguments:
        client: Django test client issuing the reset request.
        account: Active account receiving the captured email.
        remote_address: Client address assigned to reset admission.

    Returns:
        Account and token values extracted from the captured link.

    Raises:
        AssertionError: If the request or captured link is incomplete.
    """
    response = client.post(
        "/api/v1/users/reset_password/",
        {"email": account.email},
        content_type="application/json",
        REMOTE_ADDR=remote_address,
    )
    assert response.status_code == HTTPStatus.ACCEPTED
    message = cast("EmailMultiAlternatives", mail.outbox[-1])
    query = parse_qs(urlparse(message.body.split("Continue at ", maxsplit=1)[1].strip()).query)
    assert set(query) == {"account", "token"}
    return {"account": query["account"][0], "token": query["token"][0]}


def _confirm_payload(
    link: dict[str, str],
    *,
    password: str = NEW_PASSWORD,
    confirmation: str | None = None,
) -> dict[str, str]:
    """Build one complete reset-confirm request body.

    Keeps classification, validation, throttling, and recovery tests on one public request shape
    while allowing each test to vary only the replacement field it specifies.

    Arguments:
        link: Account and token values extracted from email.
        password: Replacement password to submit.
        confirmation: Optional confirmation, equal to the replacement when omitted.

    Returns:
        Complete reset-confirm body.
    """
    return {
        **link,
        "new_password": password,
        "new_password_confirm": password if confirmation is None else confirmation,
    }


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_authenticated_password_change_revokes_every_existing_credential_and_session(
    client: DjangoClient,
    django_user_model: type[User],
) -> None:
    """Change a password and invalidate every previously issued identity.

    Authenticates the change with the secondary token while retaining a refresh pair and Django
    session, then proves the caller and every other existing credential require fresh login.

    Arguments:
        client: Django test client issuing the password change.
        django_user_model: Configured custom account model.

    Returns:
        None.

    Raises:
        AssertionError: If the change fails or any prior identity remains usable.
    """
    account = django_user_model.objects.create_user(
        "password-change",
        "password-change@localforge.invalid",
        CURRENT_PASSWORD,
        is_active=True,
    )
    token = Token.objects.using("default").create(user=account)
    refresh = PrimaryRefreshToken.for_user(account)
    access = str(refresh.access_token)
    session_client = DjangoClient()
    session_client.force_login(account)

    response = client.post(
        "/api/v1/users/set_password/",
        {
            "current_password": CURRENT_PASSWORD,
            "new_password": NEW_PASSWORD,
            "new_password_confirm": NEW_PASSWORD,
        },
        content_type="application/json",
        headers={"authorization": f"Token {token.key}"},
    )

    token_probe = client.get(
        "/api/v1/users/me/",
        headers={"authorization": f"Token {token.key}"},
    )
    access_probe = client.get(
        "/api/v1/users/me/",
        headers={"authorization": f"Bearer {access}"},
    )
    refresh_probe = client.post(
        "/api/v1/jwt/refresh/",
        {"refresh": str(refresh)},
        content_type="application/json",
    )
    session_client.get("/admin/")
    account.refresh_from_db(using="default")

    assert response.status_code == HTTPStatus.NO_CONTENT
    assert response.content == b""
    assert account.check_password(NEW_PASSWORD)
    assert not account.check_password(CURRENT_PASSWORD)
    assert token_probe.status_code == HTTPStatus.UNAUTHORIZED
    assert access_probe.status_code == HTTPStatus.UNAUTHORIZED
    assert refresh_probe.status_code == HTTPStatus.UNAUTHORIZED
    assert SESSION_KEY not in cast("dict[str, object]", session_client.session)


def _post_old_password_login(account: User, login_flow: str) -> int:
    """Post one old-password login from a fresh thread-local connection.

    Keeps race orchestration outside the test's statement budget while exercising one public
    credential-issuance endpoint through an independent request client.

    Arguments:
        account: Account whose old credential is submitted.
        login_flow: Secondary-token or JSON-web-token issuance route.

    Returns:
        Public credential-issuance response status.
    """
    close_old_connections()
    try:
        route = "/api/v1/token/login/" if login_flow == "token" else "/api/v1/jwt/create/"
        response = DjangoClient().post(
            route,
            {"username": account.username, "password": CURRENT_PASSWORD},
            content_type="application/json",
            REMOTE_ADDR="192.0.2.141",
        )
        return response.status_code
    finally:
        close_old_connections()


def _post_racing_password_replacement(
    replacement_flow: str,
    authorization: Token | None,
    link: dict[str, str] | None,
) -> int:
    """Post one password replacement from a fresh thread-local connection.

    Selects authenticated change or public recovery while keeping the race test focused on ordering
    and final credential survival.

    Arguments:
        replacement_flow: Authenticated change or reset confirmation.
        authorization: Existing DRF token for authenticated change.
        link: Account and bearer fields for reset confirmation.

    Returns:
        Public password-replacement response status.
    """
    close_old_connections()
    try:
        request_client = DjangoClient()
        if replacement_flow == "set-password":
            assert authorization is not None
            response = request_client.post(
                "/api/v1/users/set_password/",
                {
                    "current_password": CURRENT_PASSWORD,
                    "new_password": NEW_PASSWORD,
                    "new_password_confirm": NEW_PASSWORD,
                },
                content_type="application/json",
                headers={"authorization": f"Token {authorization.key}"},
            )
        else:
            assert link is not None
            response = request_client.post(
                "/api/v1/users/reset_password_confirm/",
                _confirm_payload(link),
                content_type="application/json",
                REMOTE_ADDR="192.0.2.142",
            )
        return response.status_code
    finally:
        close_old_connections()


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@pytest.mark.timeout(90)
@pytest.mark.parametrize("login_flow", ["token", "jwt"])
@pytest.mark.parametrize("replacement_flow", ["set-password", "reset-confirm"])
def test_old_password_login_cannot_issue_after_password_replacement(
    client: DjangoClient,
    django_user_model: type[User],
    monkeypatch: pytest.MonkeyPatch,
    login_flow: str,
    replacement_flow: str,
) -> None:
    """Reject issuance when password replacement wins after credential verification.

    Pauses one successful lock-free verification before issuance, completes authenticated
    replacement or recovery on another connection, then proves snapshot revalidation rejects the
    stale old password for both credential protocols.

    Arguments:
        client: Django test client preparing password-replacement state.
        django_user_model: Configured custom account model.
        monkeypatch: Fixture pausing the credential-verification return boundary.
        login_flow: Secondary-token or JSON-web-token issuance route.
        replacement_flow: Authenticated change or reset confirmation racing login.

    Returns:
        None.

    Raises:
        AssertionError: If the old password can issue after the replacement commits.
    """
    suffix = uuid.uuid4().hex
    account = django_user_model.objects.create_user(
        f"password-login-race-{suffix}",
        f"password-login-race-{suffix}@localforge.invalid",
        CURRENT_PASSWORD,
        is_active=True,
    )
    authorization = None
    link = None
    if replacement_flow == "set-password":
        authorization = Token.objects.using("default").create(user=account)
    else:
        link = _request_reset_link(client, account, remote_address="192.0.2.140")

    verification_complete = Event()
    release_verification = Event()
    credential_module = (
        token_authentication_module if login_flow == "token" else jwt_authentication_module
    )
    original_verify = credential_module.verify_login_credentials

    def pause_verified_credentials(username: str, password: str) -> object:
        """Pause a successful verification before its issuance transaction begins.

        Preserves complete production hash work and exposes only the interval where a concurrent
        password replacement can invalidate the immutable authenticated snapshot.

        Arguments:
            username: Submitted account username.
            password: Submitted raw password.

        Returns:
            Real immutable authenticated snapshot.

        Raises:
            AssertionError: If the test does not release the verified request.
        """
        snapshot = original_verify(username, password)
        assert not connections["default"].in_atomic_block
        verification_complete.set()
        assert release_verification.wait(timeout=RACE_WAIT_SECONDS)
        return snapshot

    monkeypatch.setattr(
        credential_module,
        "verify_login_credentials",
        pause_verified_credentials,
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        login_future = executor.submit(_post_old_password_login, account, login_flow)
        assert verification_complete.wait(timeout=RACE_WAIT_SECONDS)
        replacement_future = executor.submit(
            _post_racing_password_replacement,
            replacement_flow,
            authorization,
            link,
        )
        replacement_status = replacement_future.result(timeout=RACE_WAIT_SECONDS)
        release_verification.set()
        login_status = login_future.result(timeout=RACE_WAIT_SECONDS)

    account.refresh_from_db(using="default")

    assert login_status == HTTPStatus.UNAUTHORIZED
    assert replacement_status == HTTPStatus.NO_CONTENT
    assert account.check_password(NEW_PASSWORD)
    assert not Token.objects.using("default").filter(user=account).exists()
    assert not OutstandingToken.objects.using("default").filter(user=account).exists()


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@pytest.mark.parametrize("login_flow", ["token", "jwt"])
def test_verified_account_disappearance_rejects_issuance(
    django_user_model: type[User],
    monkeypatch: pytest.MonkeyPatch,
    login_flow: str,
) -> None:
    """Reject credential issuance when the verified account disappears.

    Deletes an active account after complete lock-free password verification but before the
    issuance transaction, proving both protocols return the same generic authentication failure
    and persist no credential.

    Arguments:
        django_user_model: Configured custom account model.
        monkeypatch: Fixture deleting the account at the verified snapshot boundary.
        login_flow: Secondary-token or JSON-web-token issuance route.

    Returns:
        None.

    Raises:
        AssertionError: If missing locked state escapes or still issues a credential.
    """
    suffix = uuid.uuid4().hex
    account = django_user_model.objects.create_user(
        f"verified-delete-{suffix}",
        f"verified-delete-{suffix}@localforge.invalid",
        CURRENT_PASSWORD,
        is_active=True,
    )
    account_id = account.pk
    credential_module = (
        token_authentication_module if login_flow == "token" else jwt_authentication_module
    )
    original_verify = credential_module.verify_login_credentials

    def delete_after_verification(username: str, password: str) -> object:
        """Delete the matched account after returning its real authenticated snapshot.

        Commits the deletion before the view begins issuance so the locked primary lookup must
        classify missing state rather than relying on the earlier successful account read.

        Arguments:
            username: Submitted account username.
            password: Submitted raw password.

        Returns:
            Immutable snapshot whose account no longer exists.
        """
        snapshot = original_verify(username, password)
        django_user_model.objects.using("default").filter(pk=account_id).delete()
        return snapshot

    monkeypatch.setattr(
        credential_module,
        "verify_login_credentials",
        delete_after_verification,
    )
    response = DjangoClient().post(
        "/api/v1/token/login/" if login_flow == "token" else "/api/v1/jwt/create/",
        {"username": account.username, "password": CURRENT_PASSWORD},
        content_type="application/json",
        REMOTE_ADDR="192.0.2.188",
    )

    assert response.status_code == HTTPStatus.UNAUTHORIZED
    assert response.json()["code"] == ErrorCode.AUTHENTICATION_FAILED
    assert not Token.objects.using("default").filter(user_id=account_id).exists()
    assert not OutstandingToken.objects.using("default").filter(user_id=account_id).exists()


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_password_reset_request_is_indistinguishable_and_delivers_account_bound_link(
    client: DjangoClient,
    django_user_model: type[User],
) -> None:
    """Accept known and unknown addresses identically while mailing only the account.

    Posts every account-state outcome through the public boundary and inspects the captured
    multipart message, proving the response carries no account or token while only the active
    account receives a link carrying both recovery values.

    Arguments:
        client: Django test client issuing reset requests.
        django_user_model: Configured custom account model.

    Returns:
        None.

    Raises:
        AssertionError: If responses differ or the captured reset link lacks its account binding.
    """
    account = django_user_model.objects.create_user(
        "password-reset-request",
        "password-reset-request@localforge.invalid",
        CURRENT_PASSWORD,
        is_active=True,
    )
    inactive = django_user_model.objects.create_user(
        "password-reset-inactive",
        "password-reset-inactive@localforge.invalid",
        CURRENT_PASSWORD,
    )

    existing = client.post(
        "/api/v1/users/reset_password/",
        {"email": account.email},
        content_type="application/json",
        REMOTE_ADDR="192.0.2.33",
    )
    inactive_response = client.post(
        "/api/v1/users/reset_password/",
        {"email": inactive.email},
        content_type="application/json",
        REMOTE_ADDR="192.0.2.132",
    )
    unknown = client.post(
        "/api/v1/users/reset_password/",
        {"email": "unknown-password-reset@localforge.invalid"},
        content_type="application/json",
        REMOTE_ADDR="192.0.2.34",
    )

    payload = cast("dict[str, Any]", existing.json())
    message = cast("EmailMultiAlternatives", mail.outbox[0])
    text = message.body
    query = parse_qs(urlparse(text.split("Continue at ", maxsplit=1)[1].strip()).query)

    assert existing.status_code == HTTPStatus.ACCEPTED
    assert inactive_response.status_code == HTTPStatus.ACCEPTED
    assert unknown.status_code == HTTPStatus.ACCEPTED
    assert payload == {"detail": RESET_ACCEPTED_MESSAGE}
    assert inactive_response.json() == unknown.json() == payload
    assert len(mail.outbox) == 1
    assert message.to == [account.email]
    assert message.subject == "Reset your LocalForge password"
    assert message.alternatives
    assert query["account"] == [str(account.pk)]
    assert len(query["token"]) == 1
    assert query["token"][0] not in existing.content.decode()


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_password_reset_confirm_changes_password_notifies_revokes_and_rejects_replay(
    client: DjangoClient,
    django_user_model: type[User],
) -> None:
    """Complete recovery once and reject reuse of the consumed bearer.

    Requests a captured link, submits it with a validated replacement, and proves the password,
    existing credentials, notification email, new login, and replay classification at HTTP seams.

    Arguments:
        client: Django test client issuing the recovery loop.
        django_user_model: Configured custom account model.

    Returns:
        None.

    Raises:
        AssertionError: If recovery is incomplete, leaks data, or permits token replay.
    """
    account = django_user_model.objects.create_user(
        "password-reset-confirm",
        "password-reset-confirm@localforge.invalid",
        CURRENT_PASSWORD,
        is_active=True,
    )
    secondary = Token.objects.using("default").create(user=account)
    refresh = PrimaryRefreshToken.for_user(account)
    access = str(refresh.access_token)
    client.post(
        "/api/v1/users/reset_password/",
        {"email": account.email},
        content_type="application/json",
        REMOTE_ADDR="192.0.2.35",
    )
    query = parse_qs(
        urlparse(mail.outbox[0].body.split("Continue at ", maxsplit=1)[1].strip()).query
    )
    request = {
        "account": query["account"][0],
        "token": query["token"][0],
        "new_password": NEW_PASSWORD,
        "new_password_confirm": NEW_PASSWORD,
    }

    confirmed = client.post(
        "/api/v1/users/reset_password_confirm/",
        request,
        content_type="application/json",
        REMOTE_ADDR="192.0.2.36",
    )
    replayed = client.post(
        "/api/v1/users/reset_password_confirm/",
        request,
        content_type="application/json",
        REMOTE_ADDR="192.0.2.37",
    )
    token_probe = client.get(
        "/api/v1/users/me/",
        headers={"authorization": f"Token {secondary.key}"},
    )
    access_probe = client.get(
        "/api/v1/users/me/",
        headers={"authorization": f"Bearer {access}"},
    )
    refresh_probe = client.post(
        "/api/v1/jwt/refresh/",
        {"refresh": str(refresh)},
        content_type="application/json",
    )
    login = client.post(
        "/api/v1/token/login/",
        {"username": account.username, "password": NEW_PASSWORD},
        content_type="application/json",
        REMOTE_ADDR="192.0.2.38",
    )
    account.refresh_from_db(using="default")

    assert confirmed.status_code == HTTPStatus.NO_CONTENT
    assert confirmed.content == b""
    assert account.check_password(NEW_PASSWORD)
    assert token_probe.status_code == HTTPStatus.UNAUTHORIZED
    assert access_probe.status_code == HTTPStatus.UNAUTHORIZED
    assert refresh_probe.status_code == HTTPStatus.UNAUTHORIZED
    assert login.status_code == HTTPStatus.OK
    assert len(mail.outbox) == EXPECTED_RECOVERY_MESSAGE_COUNT
    assert mail.outbox[1].to == [account.email]
    assert mail.outbox[1].subject == "Your LocalForge password changed"
    assert query["token"][0] not in mail.outbox[1].body
    assert replayed.status_code == HTTPStatus.BAD_REQUEST
    assert replayed.json()["code"] == ErrorCode.PASSWORD_RESET_TOKEN_USED


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@pytest.mark.parametrize(
    ("body", "field", "message"),
    [
        pytest.param(
            {
                "current_password": "wrong-current-password",
                "new_password": NEW_PASSWORD,
                "new_password_confirm": NEW_PASSWORD,
            },
            "current_password",
            "The current password is incorrect.",
            id="incorrect-current",
        ),
        pytest.param(
            {
                "current_password": CURRENT_PASSWORD,
                "new_password": CURRENT_PASSWORD,
                "new_password_confirm": CURRENT_PASSWORD,
            },
            "new_password",
            "The new password must differ from the current password.",
            id="unchanged",
        ),
        pytest.param(
            {
                "current_password": CURRENT_PASSWORD,
                "new_password": NEW_PASSWORD,
                "new_password_confirm": f"{NEW_PASSWORD}-mismatch",
            },
            "new_password_confirm",
            "The password confirmation does not match.",
            id="confirmation",
        ),
        pytest.param(
            {
                "current_password": CURRENT_PASSWORD,
                "new_password": SHORT_PASSWORD,
                "new_password_confirm": SHORT_PASSWORD,
            },
            "new_password",
            "This password is too short. It must contain at least 8 characters.",
            id="minimum-length",
        ),
        pytest.param(
            {
                "current_password": CURRENT_PASSWORD,
                "new_password": "password",
                "new_password_confirm": "password",
            },
            "new_password",
            "This password is too common.",
            id="common",
        ),
        pytest.param(
            {
                "current_password": CURRENT_PASSWORD,
                "new_password": "123456789",
                "new_password_confirm": "123456789",
            },
            "new_password",
            "This password is entirely numeric.",
            id="numeric",
        ),
    ],
)
def test_password_change_rejects_invalid_credentials_and_replacements(
    client: DjangoClient,
    django_user_model: type[User],
    body: dict[str, str],
    field: str,
    message: str,
) -> None:
    """Reject one invalid password-change condition without mutating the credential.

    Exercises current-password verification, distinctness, confirmation, and configured validators
    through the authenticated route and keeps each message attached to its public field.

    Arguments:
        client: Django test client issuing the change.
        django_user_model: Configured custom account model.
        body: Complete change request under test.
        field: Detail field expected to carry the rejection.
        message: Independent expected validation message.

    Returns:
        None.

    Raises:
        AssertionError: If validation is skipped, misplaced, or mutates the password.
    """
    suffix = uuid.uuid4().hex
    account = django_user_model.objects.create_user(
        f"password-validation-{suffix}",
        f"password-validation-{suffix}@localforge.invalid",
        CURRENT_PASSWORD,
        is_active=True,
    )
    token = Token.objects.using("default").create(user=account)

    response = client.post(
        "/api/v1/users/set_password/",
        body,
        content_type="application/json",
        headers={"authorization": f"Token {token.key}"},
    )
    account.refresh_from_db(using="default")

    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert message in cast("dict[str, list[str]]", response.json()["details"])[field]
    assert account.check_password(CURRENT_PASSWORD)


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_password_change_requires_authentication_and_exact_body(
    client: DjangoClient,
    django_user_model: type[User],
) -> None:
    """Reject unauthenticated, undeclared, and non-object password-change input.

    Posts through the fixed route without a credential and with an authenticated caller whose
    bodies carry a forbidden selector or a non-object representation, proving identity comes only
    from authentication and validation returns exact correlated envelopes.

    Arguments:
        client: Django test client issuing the requests.
        django_user_model: Configured custom account model.

    Returns:
        None.

    Raises:
        AssertionError: If authentication or exact-field validation is bypassed.
    """
    unauthenticated = client.post(
        "/api/v1/users/set_password/",
        {},
        content_type="application/json",
    )
    account = django_user_model.objects.create_user(
        f"password-exact-{uuid.uuid4().hex}",
        f"password-exact-{uuid.uuid4().hex}@localforge.invalid",
        CURRENT_PASSWORD,
        is_active=True,
    )
    token = Token.objects.using("default").create(user=account)
    authorization = {"authorization": f"Token {token.key}"}
    extra = client.post(
        "/api/v1/users/set_password/",
        {
            "current_password": CURRENT_PASSWORD,
            "new_password": NEW_PASSWORD,
            "new_password_confirm": NEW_PASSWORD,
            "account": str(account.pk),
        },
        content_type="application/json",
        headers=authorization,
    )
    non_object = client.post(
        "/api/v1/users/set_password/",
        data="[]",
        content_type="application/json",
        headers=authorization,
    )

    assert unauthenticated.status_code == HTTPStatus.UNAUTHORIZED
    assert unauthenticated.json()["code"] == ErrorCode.NOT_AUTHENTICATED
    assert extra.status_code == non_object.status_code == HTTPStatus.BAD_REQUEST
    assert extra.json() == {
        "code": ErrorCode.VALIDATION_ERROR,
        "message": "The submitted data is invalid.",
        "details": {"account": ["This field is not allowed."]},
        "request_id": extra.headers["X-Request-ID"],
    }
    assert non_object.json() == {
        "code": ErrorCode.VALIDATION_ERROR,
        "message": "The submitted data is invalid.",
        "details": {"non_field_errors": ["Invalid data. Expected a dictionary, but got list."]},
        "request_id": non_object.headers["X-Request-ID"],
    }
    account.refresh_from_db(using="default")
    assert account.check_password(CURRENT_PASSWORD)


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_password_reset_request_rejects_extra_non_object_and_missing_bodies_exactly(
    client: DjangoClient,
) -> None:
    """Return exact validation envelopes for every reset-request shape failure.

    Exercises an undeclared field, a valid non-object JSON value, and the missing declared field
    through the public request route so serializer and schema remain on the exact ``{email}``
    contract.

    Arguments:
        client: Django test client issuing reset requests.

    Returns:
        None.

    Raises:
        AssertionError: If status, envelope, field detail, or correlation drifts.
    """
    extra = client.post(
        "/api/v1/users/reset_password/",
        {"email": "exact-reset@localforge.invalid", "account": str(uuid.uuid4())},
        content_type="application/json",
        REMOTE_ADDR="192.0.2.182",
    )
    non_object = client.post(
        "/api/v1/users/reset_password/",
        data="[]",
        content_type="application/json",
        REMOTE_ADDR="192.0.2.183",
    )
    missing = client.post(
        "/api/v1/users/reset_password/",
        {},
        content_type="application/json",
        REMOTE_ADDR="192.0.2.184",
    )

    assert [extra.status_code, non_object.status_code, missing.status_code] == [
        HTTPStatus.BAD_REQUEST
    ] * 3
    assert extra.json() == {
        "code": ErrorCode.VALIDATION_ERROR,
        "message": "The submitted data is invalid.",
        "details": {"account": ["This field is not allowed."]},
        "request_id": extra.headers["X-Request-ID"],
    }
    assert non_object.json() == {
        "code": ErrorCode.VALIDATION_ERROR,
        "message": "The submitted data is invalid.",
        "details": {"non_field_errors": ["Invalid data. Expected a dictionary, but got list."]},
        "request_id": non_object.headers["X-Request-ID"],
    }
    assert missing.json() == {
        "code": ErrorCode.VALIDATION_ERROR,
        "message": "The submitted data is invalid.",
        "details": {"email": ["This field is required."]},
        "request_id": missing.headers["X-Request-ID"],
    }


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_password_reset_confirm_rejects_extra_non_object_and_missing_bodies_exactly(
    client: DjangoClient,
) -> None:
    """Return exact validation envelopes for every reset-confirm shape failure.

    Exercises an undeclared field, a valid non-object JSON value, and all missing declared fields
    through the public confirmation route so admission and schema share one exact body contract.

    Arguments:
        client: Django test client issuing reset confirmations.

    Returns:
        None.

    Raises:
        AssertionError: If status, envelope, field detail, or correlation drifts.
    """
    complete = {
        "account": str(uuid.uuid4()),
        "token": "djang0-0123456789abcdef0123456789abcdef.nonce",
        "new_password": NEW_PASSWORD,
        "new_password_confirm": NEW_PASSWORD,
    }
    extra = client.post(
        "/api/v1/users/reset_password_confirm/",
        {**complete, "email": "forbidden@localforge.invalid"},
        content_type="application/json",
        REMOTE_ADDR="192.0.2.185",
    )
    non_object = client.post(
        "/api/v1/users/reset_password_confirm/",
        data="[]",
        content_type="application/json",
        REMOTE_ADDR="192.0.2.186",
    )
    missing = client.post(
        "/api/v1/users/reset_password_confirm/",
        {},
        content_type="application/json",
        REMOTE_ADDR="192.0.2.187",
    )

    assert [extra.status_code, non_object.status_code, missing.status_code] == [
        HTTPStatus.BAD_REQUEST
    ] * 3
    assert extra.json() == {
        "code": ErrorCode.VALIDATION_ERROR,
        "message": "The submitted data is invalid.",
        "details": {"email": ["This field is not allowed."]},
        "request_id": extra.headers["X-Request-ID"],
    }
    assert non_object.json() == {
        "code": ErrorCode.VALIDATION_ERROR,
        "message": "The submitted data is invalid.",
        "details": {"non_field_errors": ["Invalid data. Expected a dictionary, but got list."]},
        "request_id": non_object.headers["X-Request-ID"],
    }
    assert missing.json() == {
        "code": ErrorCode.VALIDATION_ERROR,
        "message": "The submitted data is invalid.",
        "details": {
            "account": ["This field is required."],
            "token": ["This field is required."],
            "new_password": ["This field is required."],
            "new_password_confirm": ["This field is required."],
        },
        "request_id": missing.headers["X-Request-ID"],
    }


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@pytest.mark.parametrize(
    ("kind", "expected_code"),
    [
        pytest.param("malformed", ErrorCode.PASSWORD_RESET_TOKEN_MALFORMED, id="malformed"),
        pytest.param("foreign", ErrorCode.PASSWORD_RESET_TOKEN_FOREIGN, id="foreign"),
        pytest.param("expired", ErrorCode.PASSWORD_RESET_TOKEN_EXPIRED, id="expired"),
    ],
)
def test_password_reset_confirm_classifies_invalid_tokens(
    client: DjangoClient,
    django_user_model: type[User],
    kind: str,
    expected_code: ErrorCode,
) -> None:
    """Return the stable token code for one malformed, foreign, or expired bearer.

    Creates reset state through the email seam and varies only the token condition before posting
    confirmation, preserving the account password and record use state after rejection.

    Arguments:
        client: Django test client issuing recovery requests.
        django_user_model: Configured custom account model.
        kind: Invalid token condition to construct.
        expected_code: Stable error code required by the contract.

    Returns:
        None.

    Raises:
        AssertionError: If classification drifts or consumes the reset record.
    """
    suffix = uuid.uuid4().hex
    account = django_user_model.objects.create_user(
        f"reset-classification-{suffix}",
        f"reset-classification-{suffix}@localforge.invalid",
        CURRENT_PASSWORD,
        is_active=True,
    )
    other = django_user_model.objects.create_user(
        f"reset-foreign-{suffix}",
        f"reset-foreign-{suffix}@localforge.invalid",
        CURRENT_PASSWORD,
        is_active=True,
    )
    if kind == "expired":
        with freeze_time("2026-01-01 00:00:00"):
            link = _request_reset_link(client, account, remote_address="192.0.2.40")
        frozen = freeze_time("2026-01-02 00:00:01")
    else:
        link = _request_reset_link(client, account, remote_address="192.0.2.41")
        frozen = freeze_time()

    if kind == "malformed":
        link["token"] = "not-a-reset-token"  # noqa: S105
    elif kind == "foreign":
        link["account"] = str(other.pk)

    with frozen:
        response = client.post(
            "/api/v1/users/reset_password_confirm/",
            _confirm_payload(link),
            content_type="application/json",
            REMOTE_ADDR="192.0.2.42",
        )
    account.refresh_from_db(using="default")

    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert response.json()["code"] == expected_code
    assert account.check_password(CURRENT_PASSWORD)
    assert (
        PasswordResetToken.objects.using("default")
        .filter(
            account=account,
            used_at__isnull=True,
        )
        .exists()
    )


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@pytest.mark.parametrize(
    ("kind", "expected_code"),
    [
        pytest.param("foreign", ErrorCode.PASSWORD_RESET_TOKEN_FOREIGN, id="foreign"),
        pytest.param("missing", ErrorCode.PASSWORD_RESET_TOKEN_FOREIGN, id="missing-account"),
        pytest.param("expired", ErrorCode.PASSWORD_RESET_TOKEN_EXPIRED, id="expired"),
        pytest.param("used", ErrorCode.PASSWORD_RESET_TOKEN_USED, id="used"),
    ],
)
def test_invalid_reset_token_classification_is_independent_of_account_attributes(
    client: DjangoClient,
    django_user_model: type[User],
    kind: str,
    expected_code: ErrorCode,
) -> None:
    """Classify invalid bearers before account-sensitive password validation.

    Submits replacements equal to a live username or the former dummy username for foreign,
    missing, expired, and used outcomes, proving admission validation cannot disclose account
    attributes or mask the stable token classification.

    Arguments:
        client: Django test client issuing reset confirmations.
        django_user_model: Configured custom account model.
        kind: Invalid token condition under test.
        expected_code: Stable token code required by the public contract.

    Returns:
        None.

    Raises:
        AssertionError: If account-sensitive validation changes the token outcome.
    """
    suffix = uuid.uuid4().hex
    issuer = django_user_model.objects.create_user(
        f"reset-classification-issuer-{suffix}",
        f"reset-classification-issuer-{suffix}@localforge.invalid",
        CURRENT_PASSWORD,
        is_active=True,
    )
    target_username = f"reset-classification-target-{suffix}"
    target = django_user_model.objects.create_user(
        target_username,
        f"{target_username}@localforge.invalid",
        CURRENT_PASSWORD,
        is_active=True,
    )
    if kind == "expired":
        with freeze_time("2026-04-01 00:00:00"):
            link = _request_reset_link(client, target, remote_address="192.0.2.133")
        frozen = freeze_time("2026-04-02 00:00:01")
        replacement = target_username
    else:
        link = _request_reset_link(client, issuer, remote_address="192.0.2.134")
        frozen = freeze_time()
        replacement = target_username
        if kind == "foreign":
            link["account"] = str(target.pk)
        elif kind == "missing":
            link["account"] = str(uuid.uuid4())
            replacement = "password-reset-candidate"
        else:
            PasswordResetToken.objects.using("default").filter(account=issuer).update(
                used_at=timezone.now()
            )

    with frozen:
        response = client.post(
            "/api/v1/users/reset_password_confirm/",
            _confirm_payload(link, password=replacement),
            content_type="application/json",
            REMOTE_ADDR="192.0.2.135",
        )

    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert response.json()["code"] == expected_code


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@override_settings(
    PASSWORD_RESET_ADDRESS_THROTTLE_RATE=LOW_RESET_RATE,
    PASSWORD_RESET_ACCOUNT_THROTTLE_RATE=LOW_RESET_RATE,
)
def test_locked_account_similarity_failure_consumes_confirm_admission(
    client: DjangoClient,
    django_user_model: type[User],
) -> None:
    """Charge admission before account-sensitive reset validation.

    Submits a valid bearer with a replacement equal to the locked account username, then retries
    from the same dimensions with a valid replacement, proving exact and account-independent
    validation is free while post-binding account policy consumes the admitted attempt.

    Arguments:
        client: Django test client issuing reset confirmations.
        django_user_model: Configured custom account model.

    Returns:
        None.

    Raises:
        AssertionError: If account-sensitive policy runs before or outside admission.
    """
    suffix = uuid.uuid4().hex
    username = f"reset-similarity-quota-{suffix}"
    account = django_user_model.objects.create_user(
        username,
        f"{username}@localforge.invalid",
        CURRENT_PASSWORD,
        is_active=True,
    )
    link = _request_reset_link(client, account, remote_address="192.0.2.136")
    address = "192.0.2.137"

    similarity = client.post(
        "/api/v1/users/reset_password_confirm/",
        _confirm_payload(link, password=username),
        content_type="application/json",
        REMOTE_ADDR=address,
    )
    retry = client.post(
        "/api/v1/users/reset_password_confirm/",
        _confirm_payload(link),
        content_type="application/json",
        REMOTE_ADDR=address,
    )

    assert similarity.status_code == HTTPStatus.BAD_REQUEST
    assert "too similar to the username" in similarity.json()["details"]["new_password"][0]
    assert retry.status_code == HTTPStatus.TOO_MANY_REQUESTS


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@pytest.mark.parametrize(
    ("password", "message"),
    [
        pytest.param(
            SHORT_PASSWORD,
            "This password is too short. It must contain at least 8 characters.",
            id="minimum-length",
        ),
        pytest.param("password", "This password is too common.", id="common"),
        pytest.param("123456789", "This password is entirely numeric.", id="numeric"),
    ],
)
def test_password_reset_confirm_applies_every_configured_validator_without_consuming_token(
    client: DjangoClient,
    django_user_model: type[User],
    password: str,
    message: str,
) -> None:
    """Reject one configured password-policy failure while preserving the reset bearer.

    Requests a real link, submits an invalid replacement, and verifies the error remains field-keyed
    and the same token can still complete recovery with a valid password.

    Arguments:
        client: Django test client issuing recovery requests.
        django_user_model: Configured custom account model.
        password: Replacement designed to trigger one validator.
        message: Independent expected validator message.

    Returns:
        None.

    Raises:
        AssertionError: If policy is skipped or the rejected attempt consumes the token.
    """
    suffix = uuid.uuid4().hex
    account = django_user_model.objects.create_user(
        f"reset-policy-{suffix}",
        f"reset-policy-{suffix}@localforge.invalid",
        CURRENT_PASSWORD,
        is_active=True,
    )
    link = _request_reset_link(client, account, remote_address="192.0.2.43")

    rejected = client.post(
        "/api/v1/users/reset_password_confirm/",
        _confirm_payload(link, password=password),
        content_type="application/json",
        REMOTE_ADDR="192.0.2.44",
    )
    accepted = client.post(
        "/api/v1/users/reset_password_confirm/",
        _confirm_payload(link),
        content_type="application/json",
        REMOTE_ADDR="192.0.2.44",
    )

    assert rejected.status_code == HTTPStatus.BAD_REQUEST
    assert message in rejected.json()["details"]["new_password"]
    assert accepted.status_code == HTTPStatus.NO_CONTENT


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_password_change_invalidates_outstanding_reset_links(
    client: DjangoClient,
    django_user_model: type[User],
) -> None:
    """Classify a reset link as used after an authenticated password change.

    Requests recovery first, changes the password through the authenticated route, and proves the
    earlier email can no longer overwrite the newer credential.

    Arguments:
        client: Django test client issuing both password flows.
        django_user_model: Configured custom account model.

    Returns:
        None.

    Raises:
        AssertionError: If the earlier reset link remains usable or loses replay classification.
    """
    suffix = uuid.uuid4().hex
    account = django_user_model.objects.create_user(
        f"reset-invalidated-{suffix}",
        f"reset-invalidated-{suffix}@localforge.invalid",
        CURRENT_PASSWORD,
        is_active=True,
    )
    link = _request_reset_link(client, account, remote_address="192.0.2.45")
    token = Token.objects.using("default").create(user=account)

    changed = client.post(
        "/api/v1/users/set_password/",
        {
            "current_password": CURRENT_PASSWORD,
            "new_password": NEW_PASSWORD,
            "new_password_confirm": NEW_PASSWORD,
        },
        content_type="application/json",
        headers={"authorization": f"Token {token.key}"},
    )
    reset = client.post(
        "/api/v1/users/reset_password_confirm/",
        _confirm_payload(link, password=THIRD_PASSWORD),
        content_type="application/json",
        REMOTE_ADDR="192.0.2.46",
    )

    assert changed.status_code == HTTPStatus.NO_CONTENT
    assert reset.status_code == HTTPStatus.BAD_REQUEST
    assert reset.json()["code"] == ErrorCode.PASSWORD_RESET_TOKEN_USED


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_email_change_and_account_deletion_invalidate_unused_reset_links(
    client: DjangoClient,
    django_user_model: type[User],
) -> None:
    """Reject reset links after their bound address or live account disappears.

    Changes one account email and deletes another after requesting their links, proving Django's
    account-state binding and nullable tombstone retain safe foreign classification.

    Arguments:
        client: Django test client issuing recovery confirmations.
        django_user_model: Configured custom account model.

    Returns:
        None.

    Raises:
        AssertionError: If changed or deleted account state still accepts an unused link.
    """
    suffix = uuid.uuid4().hex
    changed = django_user_model.objects.create_user(
        f"reset-email-{suffix}",
        f"reset-email-{suffix}@localforge.invalid",
        CURRENT_PASSWORD,
        is_active=True,
    )
    deleted = django_user_model.objects.create_user(
        f"reset-delete-{suffix}",
        f"reset-delete-{suffix}@localforge.invalid",
        CURRENT_PASSWORD,
        is_active=True,
    )
    changed_link = _request_reset_link(client, changed, remote_address="192.0.2.47")
    deleted_link = _request_reset_link(client, deleted, remote_address="192.0.2.48")
    changed.email = f"changed-{suffix}@localforge.invalid"
    changed.save(using="default", update_fields=("email", "updated_at"))
    deleted.delete(using="default")

    email_response = client.post(
        "/api/v1/users/reset_password_confirm/",
        _confirm_payload(changed_link),
        content_type="application/json",
        REMOTE_ADDR="192.0.2.49",
    )
    deletion_response = client.post(
        "/api/v1/users/reset_password_confirm/",
        _confirm_payload(deleted_link),
        content_type="application/json",
        REMOTE_ADDR="192.0.2.50",
    )

    assert email_response.json()["code"] == ErrorCode.PASSWORD_RESET_TOKEN_FOREIGN
    assert deletion_response.json()["code"] == ErrorCode.PASSWORD_RESET_TOKEN_FOREIGN


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@override_settings(
    PASSWORD_RESET_ADDRESS_THROTTLE_RATE=LOW_RESET_RATE,
    PASSWORD_RESET_ACCOUNT_THROTTLE_RATE=LOW_RESET_RATE,
    PASSWORD_RESET_MINIMUM_RESPONSE_DURATION_SECONDS=0.001,
)
def test_invalid_reset_bodies_do_not_consume_authoritative_quota(
    client: DjangoClient,
    django_user_model: type[User],
) -> None:
    """Charge reset admission only after complete body and password validation.

    Sends invalid request and confirmation bodies before valid ones from the same address and
    account dimensions, then proves only a second valid request reaches the configured limit.

    Arguments:
        client: Django test client issuing reset requests.
        django_user_model: Configured custom account model.

    Returns:
        None.

    Raises:
        AssertionError: If invalid bodies consume quota or valid repeats evade it.
    """
    suffix = uuid.uuid4().hex
    account = django_user_model.objects.create_user(
        f"reset-quota-{suffix}",
        f"reset-quota-{suffix}@localforge.invalid",
        CURRENT_PASSWORD,
        is_active=True,
    )
    address = "192.0.2.51"
    invalid_requests = [
        client.post(
            "/api/v1/users/reset_password/",
            body,
            content_type="application/json",
            REMOTE_ADDR=address,
        )
        for body in (
            {"email": "not-an-address"},
            {},
            {"email": account.email, "account": str(account.pk)},
        )
    ]
    invalid_requests.append(
        client.post(
            "/api/v1/users/reset_password/",
            data="[]",
            content_type="application/json",
            REMOTE_ADDR=address,
        )
    )
    valid_request = client.post(
        "/api/v1/users/reset_password/",
        {"email": account.email},
        content_type="application/json",
        REMOTE_ADDR=address,
    )
    repeated_request = client.post(
        "/api/v1/users/reset_password/",
        {"email": account.email},
        content_type="application/json",
        REMOTE_ADDR=address,
    )
    message = cast("EmailMultiAlternatives", mail.outbox[-1])
    query = parse_qs(urlparse(message.body.split("Continue at ", maxsplit=1)[1].strip()).query)
    link = {"account": query["account"][0], "token": query["token"][0]}
    confirm_address = "192.0.2.52"
    invalid_confirms = [
        client.post(
            "/api/v1/users/reset_password_confirm/",
            body,
            content_type="application/json",
            REMOTE_ADDR=confirm_address,
        )
        for body in (
            _confirm_payload(link, password=SHORT_PASSWORD),
            {},
            {**_confirm_payload(link), "email": account.email},
        )
    ]
    invalid_confirms.append(
        client.post(
            "/api/v1/users/reset_password_confirm/",
            data="[]",
            content_type="application/json",
            REMOTE_ADDR=confirm_address,
        )
    )
    valid_confirm = client.post(
        "/api/v1/users/reset_password_confirm/",
        _confirm_payload(link),
        content_type="application/json",
        REMOTE_ADDR=confirm_address,
    )
    repeated_confirm = client.post(
        "/api/v1/users/reset_password_confirm/",
        _confirm_payload(link, password=THIRD_PASSWORD),
        content_type="application/json",
        REMOTE_ADDR=confirm_address,
    )

    assert [response.status_code for response in invalid_requests] == [
        HTTPStatus.BAD_REQUEST
    ] * len(invalid_requests)
    assert valid_request.status_code == HTTPStatus.ACCEPTED
    assert repeated_request.status_code == HTTPStatus.TOO_MANY_REQUESTS
    assert [response.status_code for response in invalid_confirms] == [
        HTTPStatus.BAD_REQUEST
    ] * len(invalid_confirms)
    assert valid_confirm.status_code == HTTPStatus.NO_CONTENT
    assert repeated_confirm.status_code == HTTPStatus.TOO_MANY_REQUESTS


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_reset_delivery_is_single_attempt_and_revalidates_expiry(
    client: DjangoClient,
    django_user_model: type[User],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Send a reset email at most once and reject delayed execution after expiry.

    Observes one normal captured delivery and a duplicate task invocation, then suppresses a second
    publication until frozen time exceeds its lifetime and proves no late email is sent.

    Arguments:
        client: Django test client requesting reset email.
        django_user_model: Configured custom account model.
        monkeypatch: Fixture replacing only the external task-publication boundary.

    Returns:
        None.

    Raises:
        AssertionError: If redelivery or expired execution sends another message.
    """
    suffix = uuid.uuid4().hex
    account = django_user_model.objects.create_user(
        f"reset-task-{suffix}",
        f"reset-task-{suffix}@localforge.invalid",
        CURRENT_PASSWORD,
        is_active=True,
    )
    link = _request_reset_link(client, account, remote_address="192.0.2.53")
    delivered_count = len(mail.outbox)
    duplicate = send_password_reset_email(link["account"], link["token"])

    captured: list[tuple[str, str]] = []

    def capture_publication(
        *,
        args: tuple[str, str],
        expires: int,
    ) -> None:
        """Capture one task publication without executing it.

        Preserves the public delayed-call shape while preventing email work from changing the
        authoritative expiry condition under test.

        Arguments:
            args: Account and token task arguments.
            expires: Broker expiry calculated by the request.

        Returns:
            None.
        """
        assert expires > 0
        captured.append(args)

    monkeypatch.setattr(send_password_reset_email, "apply_async", capture_publication)
    with freeze_time("2026-02-01 00:00:00"):
        client.post(
            "/api/v1/users/reset_password/",
            {"email": account.email},
            content_type="application/json",
            REMOTE_ADDR="192.0.2.54",
        )
    with freeze_time("2026-02-02 00:00:01"):
        expired = send_password_reset_email(*captured[0])

    assert duplicate is False
    assert expired is False
    assert len(mail.outbox) == delivered_count


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_reset_admission_fails_closed_when_authoritative_store_is_unavailable(
    client: DjangoClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Return correlated service-unavailable when reset admission loses PostgreSQL.

    Replaces the authoritative store boundary with a database failure and verifies both public
    reset routes fail closed before token issuance or password mutation.

    Arguments:
        client: Django test client issuing reset requests.
        monkeypatch: Fixture inducing authoritative admission failure.

    Returns:
        None.

    Raises:
        AssertionError: If either route admits work without authoritative quota state.
    """

    def unavailable(*_args: object, **_kwargs: object) -> object:
        """Raise one authoritative database failure.

        Models loss of the primary admission store after request parsing so the HTTP boundary can
        prove it fails closed rather than falling back to local state.

        Arguments:
            *_args: Ignored positional call values.
            **_kwargs: Ignored keyword call values.

        Returns:
            Never returns.

        Raises:
            DatabaseError: Always.
        """
        raise DatabaseError

    monkeypatch.setattr(
        PostgresLoginThrottleStore,
        "admit",
        unavailable,
    )
    requested = client.post(
        "/api/v1/users/reset_password/",
        {"email": "closed-reset@localforge.invalid"},
        content_type="application/json",
        REMOTE_ADDR="192.0.2.55",
    )
    confirmed = client.post(
        "/api/v1/users/reset_password_confirm/",
        {
            "account": str(uuid.uuid4()),
            "token": "djang0-0123456789abcdef0123456789abcdef",
            "new_password": NEW_PASSWORD,
            "new_password_confirm": NEW_PASSWORD,
        },
        content_type="application/json",
        REMOTE_ADDR="192.0.2.56",
    )

    assert requested.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert confirmed.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert requested.json()["code"] == ErrorCode.SERVICE_UNAVAILABLE
    assert confirmed.json()["code"] == ErrorCode.SERVICE_UNAVAILABLE


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_reset_request_common_account_lookup_loss_returns_service_unavailable(
    client: DjangoClient,
    django_user_model: type[User],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reserve service unavailable for common account lookup loss.

    Allows admission to resolve the active account, fails the view's second authoritative lookup,
    and proves no reset record or mail side effect is mistaken for an accepted token-store fallback.

    Arguments:
        client: Django test client issuing the reset request.
        django_user_model: Configured custom account model.
        monkeypatch: Fixture failing only the post-admission account lookup.

    Returns:
        None.

    Raises:
        AssertionError: If common lookup loss returns accepted or leaves reset state.
    """
    suffix = uuid.uuid4().hex
    account = django_user_model.objects.create_user(
        f"reset-lookup-outage-{suffix}",
        f"reset-lookup-outage-{suffix}@localforge.invalid",
        CURRENT_PASSWORD,
        is_active=True,
    )
    original_resolve = password_management_module.resolve_password_reset_account
    calls = 0

    def fail_second_lookup(email: str) -> User | None:
        """Fail the view lookup after admission resolved the same account.

        Preserves the throttle's authoritative account dimension and isolates common request
        processing loss from token-record insertion failure.

        Arguments:
            email: Normalized reset address.

        Returns:
            Matching account for the admission lookup.

        Raises:
            DatabaseError: On the view's second lookup.
        """
        nonlocal calls
        calls += 1
        if calls == VIEW_LOOKUP_CALL:
            raise DatabaseError
        return original_resolve(email)

    monkeypatch.setattr(
        password_management_module,
        "resolve_password_reset_account",
        fail_second_lookup,
    )
    response = client.post(
        "/api/v1/users/reset_password/",
        {"email": account.email},
        content_type="application/json",
        REMOTE_ADDR="192.0.2.143",
    )

    assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert response.json()["code"] == ErrorCode.SERVICE_UNAVAILABLE
    assert not PasswordResetToken.objects.using("default").filter(account=account).exists()
    assert not mail.outbox


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.security_timing
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@override_settings(
    PASSWORD_RESET_ADDRESS_THROTTLE_RATE=HIGH_RESET_RATE,
    PASSWORD_RESET_ACCOUNT_THROTTLE_RATE=HIGH_RESET_RATE,
)
def test_password_reset_request_outcomes_meet_the_approved_timing_criterion(
    client: DjangoClient,
    django_user_model: type[User],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep every accepted reset median within the approved 5-plus-30 bound.

    Suppresses task execution, measures active, inactive, unknown, and active token-store-failure
    requests, and enforces the greater of twenty percent or ten milliseconds across all medians.

    Arguments:
        client: Django test client issuing measured reset requests.
        django_user_model: Configured custom account model.
        monkeypatch: Fixture replacing the external task-publication boundary.

    Returns:
        None.

    Raises:
        AssertionError: If response bodies differ or median timing exceeds the approved bound.
    """
    suffix = uuid.uuid4().hex
    account = django_user_model.objects.create_user(
        f"reset-timing-{suffix}",
        f"reset-timing-{suffix}@localforge.invalid",
        CURRENT_PASSWORD,
        is_active=True,
    )
    inactive = django_user_model.objects.create_user(
        f"reset-timing-inactive-{suffix}",
        f"reset-timing-inactive-{suffix}@localforge.invalid",
        CURRENT_PASSWORD,
    )
    failure_email = f"reset-timing-failure-{suffix}@localforge.invalid"
    django_user_model.objects.create_user(
        f"reset-timing-failure-{suffix}",
        failure_email,
        CURRENT_PASSWORD,
        is_active=True,
    )

    def discard_publication(
        *,
        args: tuple[str, str],
        expires: int,
    ) -> None:
        """Accept one reset task publication without running email work.

        Keeps timing measurements on request admission, token issuance, and the configured response
        floor rather than on the testing backend's eager email execution.

        Arguments:
            args: Account and token task arguments.
            expires: Broker expiry calculated by the request.

        Returns:
            None.
        """
        assert len(args) == EXPECTED_TASK_ARGUMENT_COUNT
        assert expires > 0

    monkeypatch.setattr(send_password_reset_email, "apply_async", discard_publication)
    original_create = QuerySet.create

    def fail_selected_token_insert(queryset: QuerySet[Any], **kwargs: object) -> object:
        """Fail reset-token insertion only for the selected active account.

        Preserves every normal account and admission write while forcing the accepted fallback
        path for one timing population.

        Arguments:
            queryset: Model queryset receiving the create.
            **kwargs: Field values supplied to the create.

        Returns:
            Newly created object outside the selected token insert.

        Raises:
            DatabaseError: After inserting the selected reset record.
        """
        created = original_create(queryset, **kwargs)
        candidate = kwargs.get("account")
        if (
            queryset.model is PasswordResetToken
            and isinstance(candidate, django_user_model)
            and candidate.email == failure_email
        ):
            raise DatabaseError
        return created

    monkeypatch.setattr(QuerySet, "create", fail_selected_token_insert)

    def measure(email: str, prefix: str) -> tuple[list[float], object]:
        """Measure one reset outcome through the public route.

        Performs the approved warmup and sample counts against one address outcome while assigning
        independent client dimensions to avoid rate-limit interference.

        Arguments:
            email: Known or unknown address under test.
            prefix: Address prefix keeping throttle dimensions independent.

        Returns:
            Measured durations and final public body.
        """
        durations: list[float] = []
        payload: object = None
        for index in range(TIMING_WARMUP_REQUESTS + TIMING_MEASURED_REQUESTS):
            started = time.perf_counter()
            response = client.post(
                "/api/v1/users/reset_password/",
                {"email": email},
                content_type="application/json",
                REMOTE_ADDR=f"198.51.{prefix}.{index + 1}",
            )
            elapsed = time.perf_counter() - started
            assert response.status_code == HTTPStatus.ACCEPTED
            payload = response.json()
            if index >= TIMING_WARMUP_REQUESTS:
                durations.append(elapsed)
        return durations, payload

    measured = {
        "active": measure(account.email, "100"),
        "inactive": measure(inactive.email, "101"),
        "unknown": measure(f"unknown-{suffix}@localforge.invalid", "102"),
        "token_store_failure": measure(failure_email, "103"),
    }
    medians = {name: statistics.median(samples) for name, (samples, _payload) in measured.items()}
    delta = max(medians.values()) - min(medians.values())
    allowed = max(
        max(medians.values()) * TIMING_RELATIVE_LIMIT,
        TIMING_ABSOLUTE_LIMIT_SECONDS,
    )
    timing_logger.info(
        (
            "password reset timing active=%.6fs inactive=%.6fs unknown=%.6fs "
            "token_store_failure=%.6fs delta=%.6fs allowed=%.6fs"
        ),
        medians["active"],
        medians["inactive"],
        medians["unknown"],
        medians["token_store_failure"],
        delta,
        allowed,
    )

    assert {json.dumps(payload, sort_keys=True) for _samples, payload in measured.values()} == {
        json.dumps({"detail": RESET_ACCEPTED_MESSAGE}, sort_keys=True)
    }
    assert not mail.outbox
    assert delta <= allowed


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_password_similarity_validation_uses_current_account_attributes(
    client: DjangoClient,
    django_user_model: type[User],
) -> None:
    """Apply attribute-similarity validation in both replacement flows.

    Uses each account username as the proposed replacement and verifies authenticated change and
    recovery confirmation both expose the configured validator on the replacement field.

    Arguments:
        client: Django test client issuing both password flows.
        django_user_model: Configured custom account model.

    Returns:
        None.

    Raises:
        AssertionError: If either route omits account-aware similarity validation.
    """
    username = f"similar-password-{uuid.uuid4().hex}"
    account = django_user_model.objects.create_user(
        username,
        f"{username}@localforge.invalid",
        CURRENT_PASSWORD,
        is_active=True,
    )
    token = Token.objects.using("default").create(user=account)
    link = _request_reset_link(client, account, remote_address="192.0.2.57")

    changed = client.post(
        "/api/v1/users/set_password/",
        {
            "current_password": CURRENT_PASSWORD,
            "new_password": username,
            "new_password_confirm": username,
        },
        content_type="application/json",
        headers={"authorization": f"Token {token.key}"},
    )
    reset = client.post(
        "/api/v1/users/reset_password_confirm/",
        _confirm_payload(link, password=username),
        content_type="application/json",
        REMOTE_ADDR="192.0.2.58",
    )

    assert "too similar to the username" in changed.json()["details"]["new_password"][0]
    assert "too similar to the username" in reset.json()["details"]["new_password"][0]


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_password_reset_rejects_confirmation_mismatch_and_same_password(
    client: DjangoClient,
    django_user_model: type[User],
) -> None:
    """Preserve a reset token after confirmation mismatch and unchanged replacement.

    Submits both invalid replacement conditions through HTTP and verifies neither consumes the
    account-bound token before a later valid confirmation.

    Arguments:
        client: Django test client issuing reset confirmations.
        django_user_model: Configured custom account model.

    Returns:
        None.

    Raises:
        AssertionError: If validation is misplaced or either rejected attempt consumes the token.
    """
    suffix = uuid.uuid4().hex
    account = django_user_model.objects.create_user(
        f"reset-distinct-{suffix}",
        f"reset-distinct-{suffix}@localforge.invalid",
        CURRENT_PASSWORD,
        is_active=True,
    )
    link = _request_reset_link(client, account, remote_address="192.0.2.59")

    mismatch = client.post(
        "/api/v1/users/reset_password_confirm/",
        _confirm_payload(link, confirmation=f"{NEW_PASSWORD}-mismatch"),
        content_type="application/json",
        REMOTE_ADDR="192.0.2.60",
    )
    unchanged = client.post(
        "/api/v1/users/reset_password_confirm/",
        _confirm_payload(link, password=CURRENT_PASSWORD),
        content_type="application/json",
        REMOTE_ADDR="192.0.2.61",
    )
    accepted = client.post(
        "/api/v1/users/reset_password_confirm/",
        _confirm_payload(link),
        content_type="application/json",
        REMOTE_ADDR="192.0.2.62",
    )

    assert mismatch.json()["details"]["new_password_confirm"] == [
        "The password confirmation does not match."
    ]
    assert unchanged.json()["details"]["new_password"] == [
        "The new password must differ from the current password."
    ]
    assert accepted.status_code == HTTPStatus.NO_CONTENT


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_used_reset_tombstone_survives_account_deletion(
    client: DjangoClient,
    django_user_model: type[User],
) -> None:
    """Keep replay classified as used after the live account is deleted.

    Completes recovery, deletes the account, and submits the consumed bearer again, proving the
    nullable record retains immutable subject and use state without retaining token material.

    Arguments:
        client: Django test client issuing recovery requests.
        django_user_model: Configured custom account model.

    Returns:
        None.

    Raises:
        AssertionError: If deletion loses used-token classification.
    """
    suffix = uuid.uuid4().hex
    account = django_user_model.objects.create_user(
        f"reset-tombstone-{suffix}",
        f"reset-tombstone-{suffix}@localforge.invalid",
        CURRENT_PASSWORD,
        is_active=True,
    )
    link = _request_reset_link(client, account, remote_address="192.0.2.63")
    account_id = account.pk
    first = client.post(
        "/api/v1/users/reset_password_confirm/",
        _confirm_payload(link),
        content_type="application/json",
        REMOTE_ADDR="192.0.2.64",
    )
    account.delete(using="default")
    replay = client.post(
        "/api/v1/users/reset_password_confirm/",
        _confirm_payload(link, password=THIRD_PASSWORD),
        content_type="application/json",
        REMOTE_ADDR="192.0.2.65",
    )

    assert first.status_code == HTTPStatus.NO_CONTENT
    assert replay.json()["code"] == ErrorCode.PASSWORD_RESET_TOKEN_USED
    record = PasswordResetToken.objects.using("default").get(subject_id=account_id)
    assert record.account is None
    assert record.used_at is not None
    assert link["token"] not in record.digest
    assert str(record) == f"{account_id}:{record.pk}"


@pytest.mark.integration
@pytest.mark.api_runtime_exempt
@pytest.mark.services("postgres")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_password_routes_document_their_complete_status_contract() -> None:
    """List every success, framework, policy, throttle, and dependency response.

    Generates the public OpenAPI document and verifies the three fixed password routes expose no
    extra methods and list every reachable response with examples supplied by their annotations.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If a route, method, status, or response example is missing.
    """
    schema = cast(
        "dict[str, Any]",
        SchemaGenerator().get_schema(request=None, public=True),  # type: ignore[no-untyped-call]
    )
    paths = cast("dict[str, Any]", schema["paths"])
    expected = {
        "/api/v1/users/set_password/": {
            "204",
            "400",
            "401",
            "405",
            "406",
            "413",
            "415",
            "429",
            "500",
            "503",
        },
        "/api/v1/users/reset_password/": {
            "202",
            "400",
            "405",
            "406",
            "413",
            "415",
            "429",
            "500",
            "503",
        },
        "/api/v1/users/reset_password_confirm/": {
            "204",
            "400",
            "405",
            "406",
            "413",
            "415",
            "429",
            "500",
            "503",
        },
    }
    for route, statuses in expected.items():
        operation = cast("dict[str, Any]", paths[route])
        responses = cast("dict[str, Any]", operation["post"]["responses"])
        assert set(operation) == {"post"}
        assert set(responses) == statuses
        for status, response in responses.items():
            if status.startswith("2"):
                continue
            content = cast("dict[str, Any]", response)["content"]["application/json"]
            assert cast("dict[str, Any]", content)["examples"]

    request_operation = cast(
        "dict[str, Any]",
        paths["/api/v1/users/reset_password/"]["post"],
    )
    confirm_operation = cast(
        "dict[str, Any]",
        paths["/api/v1/users/reset_password_confirm/"]["post"],
    )
    assert "token insert failure" in request_operation["description"].lower()
    assert "account lookup" in request_operation["responses"]["503"]["description"].lower()
    assert "account-independent" in confirm_operation["description"]


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@pytest.mark.parametrize(
    "case",
    [
        pytest.param("set-password", id="set-password"),
        pytest.param("reset-password", id="reset-password"),
        pytest.param("reset-password-confirm", id="reset-password-confirm"),
    ],
)
def test_password_operation_observed_statuses_exactly_match_its_documented_contract(  # noqa: C901, PLR0915
    client: DjangoClient,
    django_user_model: type[User],
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    """Compare every reachable password-operation status with OpenAPI.

    Drives one fixed password route through success and every applicable framework, authentication,
    throttle, dependency, and unexpected-failure boundary before comparing the observed status set.

    Arguments:
        client: Django test client issuing versioned requests.
        django_user_model: Configured custom account model.
        monkeypatch: Fixture inducing contained operation failures.
        case: Password operation selected by the parameter case.

    Returns:
        None.

    Raises:
        AssertionError: If observed and documented statuses differ.
    """
    suffix = uuid.uuid4().hex
    route = {
        "set-password": "/api/v1/users/set_password/",
        "reset-password": "/api/v1/users/reset_password/",
        "reset-password-confirm": "/api/v1/users/reset_password_confirm/",
    }[case]
    view_class = {
        "set-password": password_management_module.PasswordChangeView,
        "reset-password": password_management_module.PasswordResetRequestView,
        "reset-password-confirm": password_management_module.PasswordResetConfirmView,
    }[case]
    confirmation_link: dict[str, str] | None = None
    if case == "reset-password-confirm":
        account = django_user_model.objects.create_user(
            f"password-contract-confirm-{suffix}",
            f"password-contract-confirm-{suffix}@localforge.invalid",
            CURRENT_PASSWORD,
            is_active=True,
        )
        with override_settings(
            PASSWORD_RESET_ADDRESS_THROTTLE_RATE=HIGH_RESET_RATE,
            PASSWORD_RESET_ACCOUNT_THROTTLE_RATE=HIGH_RESET_RATE,
        ):
            confirmation_link = _request_reset_link(
                client,
                account,
                remote_address=f"2001:db8::{uuid.uuid4().int & 0xFFFF:x}",
            )

    def invoke(
        data: object,
        *,
        accept: str | None = None,
        content_type: str = "application/json",
        remote_address: str | None = None,
        authenticated: bool = True,
    ) -> object:
        """Invoke the selected password operation through its public HTTP seam.

        Creates a fresh authenticated account for password replacement while public recovery routes
        reuse only their stable request values, preventing one response from mutating another.

        Arguments:
            data: Request body passed to the Django client.
            accept: Optional response media type.
            content_type: Submitted request media type.
            remote_address: Optional unique client address.
            authenticated: Whether a protected operation receives a token.

        Returns:
            Django client response.

        Raises:
            AssertionError: If confirmation setup is unexpectedly absent.
        """
        headers = {} if accept is None else {"accept": accept}
        if case == "set-password" and authenticated:
            identity = uuid.uuid4().hex
            account = django_user_model.objects.create_user(
                f"password-contract-change-{identity}",
                f"password-contract-change-{identity}@localforge.invalid",
                CURRENT_PASSWORD,
                is_active=True,
            )
            token = Token.objects.using("default").create(user=account)
            headers["authorization"] = f"Token {token.key}"
        if remote_address is None:
            return client.post(
                route,
                data,
                content_type=content_type,
                headers=headers,
            )
        return client.post(
            route,
            data,
            content_type=content_type,
            headers=headers,
            REMOTE_ADDR=remote_address,
        )

    valid_data: object
    if case == "set-password":
        valid_data = {
            "current_password": CURRENT_PASSWORD,
            "new_password": NEW_PASSWORD,
            "new_password_confirm": NEW_PASSWORD,
        }
    elif case == "reset-password":
        valid_data = {"email": f"password-contract-{suffix}@localforge.invalid"}
    else:
        assert confirmation_link is not None
        valid_data = _confirm_payload(confirmation_link)

    invalid = invoke({})
    method_headers: dict[str, str] = {}
    if case == "set-password":
        method_account = django_user_model.objects.create_user(
            f"password-contract-method-{suffix}",
            f"password-contract-method-{suffix}@localforge.invalid",
            CURRENT_PASSWORD,
            is_active=True,
        )
        method_token = Token.objects.using("default").create(user=method_account)
        method_headers["authorization"] = f"Token {method_token.key}"
    method_not_allowed = client.get(route, headers=method_headers)
    not_acceptable = invoke(valid_data, accept="text/plain")
    with override_settings(API_REQUEST_BODY_MAX_BYTES=1):
        too_large = invoke("oversized", content_type="text/plain")
    unsupported = invoke("unsupported", content_type="text/plain")
    throttle_address = f"2001:db8::{uuid.uuid4().int & 0xFFFF:x}"
    throttle_headers: dict[str, str] = {}
    throttled: object
    if case == "set-password":
        throttle_account = django_user_model.objects.create_user(
            f"password-contract-throttle-{suffix}",
            f"password-contract-throttle-{suffix}@localforge.invalid",
            CURRENT_PASSWORD,
            is_active=True,
        )
        throttle_token = Token.objects.using("default").create(user=throttle_account)
        throttle_headers["authorization"] = f"Token {throttle_token.key}"
    with override_settings(
        API_ANONYMOUS_THROTTLE_RATE="1000/minute",
        API_AUTHENTICATION_THROTTLE_RATE="1/minute",
        PASSWORD_RESET_ACCOUNT_THROTTLE_RATE=HIGH_RESET_RATE,
        PASSWORD_RESET_ADDRESS_THROTTLE_RATE=(
            HIGH_RESET_RATE if case == "set-password" else LOW_RESET_RATE
        ),
    ):
        if case == "set-password":
            client.post(
                route,
                {},
                content_type="application/json",
                headers=throttle_headers,
                REMOTE_ADDR=throttle_address,
            )
            throttled = client.post(
                route,
                {},
                content_type="application/json",
                headers=throttle_headers,
                REMOTE_ADDR=throttle_address,
            )
        elif case == "reset-password":
            invoke(
                {"email": f"password-throttle-first-{suffix}@localforge.invalid"},
                remote_address=throttle_address,
            )
            throttled = invoke(
                {"email": f"password-throttle-second-{suffix}@localforge.invalid"},
                remote_address=throttle_address,
            )
        else:
            throttle_account = django_user_model.objects.create_user(
                f"password-confirm-throttle-{suffix}",
                f"password-confirm-throttle-{suffix}@localforge.invalid",
                CURRENT_PASSWORD,
                is_active=True,
            )
            throttle_link = _request_reset_link(
                client,
                throttle_account,
                remote_address=f"2001:db8::{uuid.uuid4().int & 0xFFFF:x}",
            )
            throttle_payload = _confirm_payload(throttle_link)
            client.post(
                route,
                throttle_payload,
                content_type="application/json",
                REMOTE_ADDR=throttle_address,
            )
            throttled = client.post(
                route,
                throttle_payload,
                content_type="application/json",
                REMOTE_ADDR=throttle_address,
            )

    def fail_unexpected(_view: object, _request: object) -> None:
        """Raise one contained password-operation server failure.

        Replaces only the selected view method after framework admission so routing, authentication,
        and response containment stay production-shaped.

        Arguments:
            _view: Selected password view instance.
            _request: Admitted REST request.

        Returns:
            Never returns.

        Raises:
            RuntimeError: Always.
        """
        message = "induced password operation failure"
        raise RuntimeError(message)

    def fail_unavailable(_view: object, _request: object) -> None:
        """Raise one password-operation dependency failure.

        Replaces only the selected view method after framework admission so the shared correlated
        unavailable response remains executable for every route.

        Arguments:
            _view: Selected password view instance.
            _request: Admitted REST request.

        Returns:
            Never returns.

        Raises:
            ServiceUnavailable: Always.
        """
        raise ServiceUnavailable

    with monkeypatch.context() as failure_patch:
        failure_patch.setattr(view_class, "post", fail_unexpected)
        client.raise_request_exception = False
        unexpected = invoke(valid_data)

    with monkeypatch.context() as outage_patch:
        outage_patch.setattr(view_class, "post", fail_unavailable)
        unavailable = invoke(valid_data)

    success = invoke(valid_data)
    responses = [
        success,
        invalid,
        method_not_allowed,
        not_acceptable,
        too_large,
        unsupported,
        throttled,
        unexpected,
        unavailable,
    ]
    if case == "set-password":
        responses.append(invoke(valid_data, authenticated=False))
    observed = {cast("Any", response).status_code for response in responses}
    schema = cast(
        "dict[str, Any]",
        SchemaGenerator().get_schema(request=None, public=True),  # type: ignore[no-untyped-call]
    )
    documented = {
        int(status)
        for status in cast(
            "dict[str, Any]",
            cast("dict[str, Any]", schema["paths"])[route]["post"]["responses"],
        )
    }

    assert observed == documented


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_password_routes_reach_negotiation_media_and_method_errors(
    client: DjangoClient,
    django_user_model: type[User],
) -> None:
    """Reach framework errors documented by every password route.

    Uses authenticated change for method handling and public reset routes for negotiation and media
    rejection, proving the shared envelope remains active outside view code.

    Arguments:
        client: Django test client issuing framework-level requests.
        django_user_model: Configured custom account model.

    Returns:
        None.

    Raises:
        AssertionError: If method, accept, or content-type handling bypasses the envelope.
    """
    suffix = uuid.uuid4().hex
    account = django_user_model.objects.create_user(
        f"password-framework-{suffix}",
        f"password-framework-{suffix}@localforge.invalid",
        CURRENT_PASSWORD,
        is_active=True,
    )
    token = Token.objects.using("default").create(user=account)

    method = client.get(
        "/api/v1/users/set_password/",
        headers={"authorization": f"Token {token.key}"},
    )
    reset_method = client.get("/api/v1/users/reset_password/")
    confirm_method = client.get("/api/v1/users/reset_password_confirm/")
    negotiation = client.post(
        "/api/v1/users/reset_password/",
        {"email": account.email},
        content_type="application/json",
        headers={"accept": "application/xml"},
    )
    media = client.post(
        "/api/v1/users/reset_password_confirm/",
        "account=value",
        content_type="text/plain",
    )

    assert method.json()["code"] == ErrorCode.METHOD_NOT_ALLOWED
    assert reset_method.json()["code"] == ErrorCode.METHOD_NOT_ALLOWED
    assert confirm_method.json()["code"] == ErrorCode.METHOD_NOT_ALLOWED
    assert negotiation.json()["code"] == ErrorCode.NOT_ACCEPTABLE
    assert media.json()["code"] == ErrorCode.UNSUPPORTED_MEDIA_TYPE


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_password_reset_publication_contains_failures_without_sensitive_logs(
    client: DjangoClient,
    django_user_model: type[User],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Contain broker and token-expiry publication failures without leaking values.

    Induces an external publication failure through the HTTP request and directly presents an
    expired queued value, then verifies safe accepted behavior and credential-free log records.

    Arguments:
        client: Django test client issuing the reset request.
        django_user_model: Configured custom account model.
        monkeypatch: Fixture replacing the external task-publication boundary.
        caplog: Captured structured logging records.

    Returns:
        None.

    Raises:
        AssertionError: If publication escapes, logs sensitive data, or sends a message.
    """
    suffix = uuid.uuid4().hex
    account = django_user_model.objects.create_user(
        f"reset-publication-{suffix}",
        f"reset-publication-{suffix}@localforge.invalid",
        CURRENT_PASSWORD,
        is_active=True,
    )

    def fail_publication(*_args: object, **_kwargs: object) -> None:
        """Raise one external queue publication failure.

        Models a broker outage after authoritative token commit so the request can prove accepted
        enumeration-resistant behavior and credential-free failure logging.

        Arguments:
            *_args: Ignored positional values.
            **_kwargs: Ignored keyword values.

        Returns:
            Never returns.

        Raises:
            OSError: Always.
        """
        message = "broker unavailable"
        raise OSError(message)

    monkeypatch.setattr(send_password_reset_email, "apply_async", fail_publication)
    with caplog.at_level(logging.ERROR, logger=password_management_module.__name__):
        response = client.post(
            "/api/v1/users/reset_password/",
            {"email": account.email},
            content_type="application/json",
            REMOTE_ADDR="192.0.2.66",
        )
    record = PasswordResetToken.objects.using("default").get(account=account)
    log_text = " ".join(record.getMessage() for record in caplog.records)

    assert response.status_code == HTTPStatus.ACCEPTED
    assert account.email not in log_text
    assert record.digest not in log_text
    assert "password reset email publication failed" in log_text.lower()


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@pytest.mark.parametrize(
    "case",
    [
        pytest.param(("active", True, 1), id="active-normal"),
        pytest.param(("active", False, 0), id="active-token-store-failure"),
        pytest.param(("inactive", True, 0), id="inactive-normal"),
        pytest.param(("inactive", False, 0), id="inactive-token-store-failure"),
        pytest.param(("unknown", True, 0), id="unknown-normal"),
        pytest.param(("unknown", False, 0), id="unknown-token-store-failure"),
    ],
)
@override_settings(PASSWORD_RESET_MINIMUM_RESPONSE_DURATION_SECONDS=0.001)
def test_password_reset_request_token_store_matrix_remains_indistinguishable(
    client: DjangoClient,
    django_user_model: type[User],
    monkeypatch: pytest.MonkeyPatch,
    case: tuple[str, bool, int],
) -> None:
    """Keep token-store-only failure indistinguishable across every account state.

    Runs the active, inactive, and unknown request matrix with and without a token-record insert
    failure, proving every case returns the accepted contract, publishes one real-or-dummy task,
    leaves no partial reset record, and sends no synchronous email.

    Arguments:
        client: Django test client issuing the reset request.
        django_user_model: Configured custom account model.
        monkeypatch: Fixture inducing token persistence failure.
        case: Account state, token-store availability, and expected record count.

    Returns:
        None.

    Raises:
        AssertionError: If any public, state, publication, or mail outcome differs.
    """
    account_state, token_store_available, expected_records = case
    suffix = uuid.uuid4().hex
    email = f"reset-persistence-{suffix}@localforge.invalid"
    account = None
    if account_state != "unknown":
        account = django_user_model.objects.create_user(
            f"reset-persistence-{suffix}",
            email,
            CURRENT_PASSWORD,
            is_active=account_state == "active",
        )
    published: list[tuple[str, str]] = []

    def capture_publication(
        *,
        args: tuple[str, str],
        expires: int,
    ) -> None:
        """Capture one real-or-dummy reset publication.

        Preserves the delayed task seam without executing SMTP, allowing the matrix to compare
        publication count independently of authoritative token state.

        Arguments:
            args: Account and token task arguments.
            expires: Positive broker lifetime remaining.

        Returns:
            None.

        Raises:
            AssertionError: If publication carries an invalid shape.
        """
        assert len(args) == EXPECTED_TASK_ARGUMENT_COUNT
        assert expires > 0
        published.append(args)

    monkeypatch.setattr(send_password_reset_email, "apply_async", capture_publication)
    if not token_store_available:
        original_create = QuerySet.create

        def fail_after_insert(queryset: QuerySet[Any], **kwargs: object) -> object:
            """Raise after inserting a reset record inside its savepoint.

            Leaves non-reset model creation unchanged and simulates the strongest partial-write
            case so the request transaction must roll back the inserted record before dummy work.

            Arguments:
                queryset: Model queryset receiving the create operation.
                **kwargs: Field values for the new model.

            Returns:
                Newly created object for models outside reset-token storage.

            Raises:
                DatabaseError: After a password-reset token insert.
            """
            created = original_create(queryset, **kwargs)
            if queryset.model is PasswordResetToken:
                raise DatabaseError
            return created

        monkeypatch.setattr(QuerySet, "create", fail_after_insert)

    response = client.post(
        "/api/v1/users/reset_password/",
        {"email": email},
        content_type="application/json",
        REMOTE_ADDR="192.0.2.67",
    )

    assert response.status_code == HTTPStatus.ACCEPTED
    assert response.json() == {"detail": RESET_ACCEPTED_MESSAGE}
    assert len(published) == 1
    assert PasswordResetToken.objects.using("default").filter(account=account).count() == (
        expected_records
    )
    if account_state == "active" and token_store_available:
        assert published[0][0] == str(cast("User", account).pk)
    elif account is not None:
        assert published[0][0] != str(account.pk)
    assert not mail.outbox


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@override_settings(
    PASSWORD_RESET_MINIMUM_RESPONSE_DURATION_SECONDS=(
        PERSISTENCE_LOG_MINIMUM_RESPONSE_DURATION_SECONDS
    )
)
def test_password_reset_token_store_failure_is_correlated_and_redacted(
    client: DjangoClient,
    django_user_model: type[User],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Record accepted reset-token persistence loss without exposing request secrets.

    Forces the active-account token insert to fail with credential-bearing diagnostics, then proves
    the endpoint retains its timed accepted contract while one fixed structured operational event
    carries only request correlation and the bounded exception type.

    Arguments:
        client: Django test client issuing the reset request.
        django_user_model: Configured custom account model.
        monkeypatch: Fixture inducing reset-token persistence failure.
        caplog: Fixture collecting the operational record.

    Returns:
        None.

    Raises:
        AssertionError: If the response changes or sensitive persistence data reaches the log.
    """
    suffix = uuid.uuid4().hex
    email = f"reset-log-{suffix}@localforge.invalid"
    account = django_user_model.objects.create_user(
        f"reset-log-{suffix}",
        email,
        CURRENT_PASSWORD,
        is_active=True,
    )
    submitted_token = f"token-{secrets.token_urlsafe(24)}"
    submitted_digest = secrets.token_hex(32)
    submitted_password = f"password-{secrets.token_urlsafe(24)}"
    submitted_bearer = f"bearer-{secrets.token_urlsafe(24)}"
    original_create = QuerySet.create

    def fail_reset_token_insert(queryset: QuerySet[Any], **kwargs: object) -> object:
        """Fail only reset-token persistence with unsafe database diagnostics.

        Leaves admission writes intact and raises from the token insert with every prohibited value
        class represented, so production formatting proves none can escape.

        Arguments:
            queryset: Model queryset receiving the create operation.
            **kwargs: Field values supplied to the create.

        Returns:
            Newly created object for non-reset models.

        Raises:
            DatabaseError: For password-reset token persistence.
        """
        if queryset.model is PasswordResetToken:
            message = (
                f"account_id={account.pk} email={email} token={submitted_token} "
                f"digest={submitted_digest} password={submitted_password} bearer={submitted_bearer}"
            )
            raise DatabaseError(message)
        return original_create(queryset, **kwargs)

    monkeypatch.setattr(QuerySet, "create", fail_reset_token_insert)
    started = time.perf_counter()
    with caplog.at_level(logging.ERROR, logger=password_management_module.__name__):
        response = client.post(
            "/api/v1/users/reset_password/",
            {"email": email},
            content_type="application/json",
            REMOTE_ADDR="192.0.2.68",
        )
    elapsed = time.perf_counter() - started
    failure_records = [
        record
        for record in caplog.records
        if record.getMessage() == "Password reset token persistence failed"
    ]

    assert response.status_code == HTTPStatus.ACCEPTED
    assert response.json() == {"detail": RESET_ACCEPTED_MESSAGE}
    assert elapsed >= PERSISTENCE_LOG_MINIMUM_RESPONSE_DURATION_SECONDS
    assert len(failure_records) == 1
    failure_record = failure_records[0]
    rendered = StructuredFormatter().format(failure_record)
    failure_fields = vars(failure_record)
    assert failure_fields["operation_error_type"] == "DatabaseError"
    assert failure_fields["request_id"] == response.headers[REQUEST_ID_HEADER]
    assert set(json.loads(rendered)) >= {
        "level",
        "logger",
        "message",
        "operation_error_type",
        "request_id",
        "timestamp",
    }
    assert str(account.pk) not in rendered
    assert email not in rendered
    assert submitted_token not in rendered
    assert submitted_digest not in rendered
    assert submitted_password not in rendered
    assert submitted_bearer not in rendered
    assert not PasswordResetToken.objects.using("default").filter(account=account).exists()
    assert not mail.outbox


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@pytest.mark.parametrize(
    "mutation",
    ["delete-account", "deactivate-account", "change-email", "use-token"],
)
def test_password_reset_task_revalidates_authoritative_state_before_delivery(
    client: DjangoClient,
    django_user_model: type[User],
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    """Reject one deleted, inactive, changed-address, or used task delivery.

    Captures the queued public task arguments before execution, mutates authoritative account or
    token state, and verifies no message crosses the email seam.

    Arguments:
        client: Django test client scheduling reset work.
        django_user_model: Configured custom account model.
        monkeypatch: Fixture capturing the task-publication boundary.
        mutation: Authoritative state transition applied before task execution.

    Returns:
        None.

    Raises:
        AssertionError: If stale task state can deliver a reset message.
    """
    suffix = uuid.uuid4().hex
    account = django_user_model.objects.create_user(
        f"reset-task-state-{suffix}",
        f"reset-task-state-{suffix}@localforge.invalid",
        CURRENT_PASSWORD,
        is_active=True,
    )
    captured: list[tuple[str, str]] = []

    def capture_publication(
        *,
        args: tuple[str, str],
        expires: int,
    ) -> None:
        """Capture queued task arguments without executing delivery.

        Preserves account, bearer, and broker-expiry values so the test can mutate authoritative
        state before invoking the same public task seam.

        Arguments:
            args: Account and token task arguments.
            expires: Positive broker expiry.

        Returns:
            None.
        """
        assert expires > 0
        captured.append(args)

    monkeypatch.setattr(send_password_reset_email, "apply_async", capture_publication)
    client.post(
        "/api/v1/users/reset_password/",
        {"email": account.email},
        content_type="application/json",
        REMOTE_ADDR="192.0.2.68",
    )
    if mutation == "delete-account":
        account.delete(using="default")
    elif mutation == "deactivate-account":
        account.is_active = False
        account.save(using="default", update_fields=("is_active", "updated_at"))
    elif mutation == "change-email":
        account.email = f"changed-task-{suffix}@localforge.invalid"
        account.save(using="default", update_fields=("email", "updated_at"))
    else:
        PasswordResetToken.objects.using("default").filter(account=account).update(
            used_at=timezone.now()
        )

    delivered = send_password_reset_email(*captured[0])

    assert delivered is False
    assert not mail.outbox


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_password_reset_task_contains_email_rejection(
    client: DjangoClient,
    django_user_model: type[User],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Leave delivery unrecorded when the email boundary rejects a message.

    Captures queued task arguments, makes the failure-safe email adapter report rejection, and
    verifies the task returns false without marking SMTP delivery complete.

    Arguments:
        client: Django test client scheduling reset work.
        django_user_model: Configured custom account model.
        monkeypatch: Fixture controlling publication and email boundaries.

    Returns:
        None.

    Raises:
        AssertionError: If rejected email is reported as delivered.
    """
    suffix = uuid.uuid4().hex
    account = django_user_model.objects.create_user(
        f"reset-email-reject-{suffix}",
        f"reset-email-reject-{suffix}@localforge.invalid",
        CURRENT_PASSWORD,
        is_active=True,
    )
    captured: list[tuple[str, str]] = []

    def capture_publication(
        *,
        args: tuple[str, str],
        expires: int,
    ) -> None:
        """Capture queued task arguments without executing delivery.

        Separates request-time issuance from worker-time email acceptance so rejection can be
        observed without replacing account or token state.

        Arguments:
            args: Account and token task arguments.
            expires: Positive broker expiry.

        Returns:
            None.
        """
        assert expires > 0
        captured.append(args)

    monkeypatch.setattr(send_password_reset_email, "apply_async", capture_publication)

    def reject_email(*_args: object, **_kwargs: object) -> bool:
        """Reject one email delivery without raising.

        Models the failure-safe application email adapter declining a message after the task has
        durably claimed its sole attempt.

        Arguments:
            *_args: Ignored positional email values.
            **_kwargs: Ignored keyword email values.

        Returns:
            False.
        """
        return False

    monkeypatch.setattr(account_tasks_module, "send_application_email", reject_email)
    client.post(
        "/api/v1/users/reset_password/",
        {"email": account.email},
        content_type="application/json",
        REMOTE_ADDR="192.0.2.69",
    )
    delivered = send_password_reset_email(*captured[0])
    record = PasswordResetToken.objects.using("default").get(account=account)

    assert delivered is False
    assert record.delivery_claimed_at is not None
    assert record.delivered_at is None


@pytest.mark.integration
@pytest.mark.api_runtime_exempt
@pytest.mark.services("postgres")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_password_reset_task_rejects_malformed_identity_and_token() -> None:
    """Reject malformed task arguments before authoritative lookup.

    Invokes the public Celery task seam with invalid identity and token shapes and verifies neither
    can escape as an exception or cross the email boundary.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If malformed task input is accepted.
    """
    invalid_identity = send_password_reset_email(
        "not-an-account",
        "djang0-0123456789abcdef0123456789abcdef.nonce",
    )
    invalid_token = send_password_reset_email(str(uuid.uuid4()), "invalid-core.nonce")
    empty_nonce = send_password_reset_email(
        str(uuid.uuid4()),
        "djang0-0123456789abcdef0123456789abcdef.",
    )

    assert invalid_identity is False
    assert invalid_token is False
    assert empty_nonce is False


@pytest.mark.integration
@pytest.mark.api_runtime_exempt
@pytest.mark.services("postgres")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@pytest.mark.parametrize(
    "invalid_value",
    [
        pytest.param(None, id="null"),
        pytest.param(False, id="boolean"),
        pytest.param(7, id="integer"),
        pytest.param([], id="list"),
        pytest.param({}, id="mapping"),
    ],
)
@pytest.mark.parametrize("invalid_field", ["account", "token"])
def test_password_reset_task_rejects_non_string_payloads_before_state_or_smtp(
    monkeypatch: pytest.MonkeyPatch,
    invalid_value: object,
    invalid_field: str,
) -> None:
    """Reject non-string task payloads before parsing or external work.

    Passes every JSON-reachable non-string shape as account or token input while replacing database
    and SMTP boundaries with failures, proving malformed queue messages return false without
    authoritative reads, claims, or delivery attempts.

    Arguments:
        monkeypatch: Fixture rejecting database and SMTP boundary access.
        invalid_value: Non-string JSON-compatible task value.
        invalid_field: Account or token payload position receiving the value.

    Returns:
        None.

    Raises:
        AssertionError: If malformed input reaches database or SMTP work.
    """

    def reject_boundary(*_args: object, **_kwargs: object) -> None:
        """Reject any database or SMTP work.

        Makes early payload type validation observable by failing if parsing continues into an
        authoritative claim or application email call.

        Arguments:
            *_args: Unexpected positional boundary values.
            **_kwargs: Unexpected keyword boundary values.

        Returns:
            Never returns.

        Raises:
            AssertionError: Always.
        """
        message = "non-string reset task payload reached external work"
        raise AssertionError(message)

    monkeypatch.setattr(
        account_tasks_module,
        "_claim_password_reset_delivery",
        reject_boundary,
    )
    monkeypatch.setattr(account_tasks_module, "send_application_email", reject_boundary)
    account_value: object = str(uuid.uuid4())
    token_value: object = secrets.token_urlsafe(32)
    if invalid_field == "account":
        account_value = invalid_value
    else:
        token_value = invalid_value

    delivered = cast("Any", send_password_reset_email)(account_value, token_value)

    assert delivered is False


@pytest.mark.integration
@pytest.mark.api_runtime_exempt
@pytest.mark.services("postgres")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_expired_reset_publication_is_discarded_before_queueing(
    django_user_model: type[User],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Discard a delayed reset publication whose token lifetime is exhausted.

    Creates a real Django token under frozen time, advances beyond the configured timeout, and
    calls the publication boundary while proving the queue adapter is never invoked.

    Arguments:
        django_user_model: Configured custom account model.
        monkeypatch: Fixture rejecting any unexpected queue publication.

    Returns:
        None.

    Raises:
        AssertionError: If expired work reaches the broker boundary.
    """
    account = django_user_model.objects.create_user(
        f"expired-publication-{uuid.uuid4().hex}",
        f"expired-publication-{uuid.uuid4().hex}@localforge.invalid",
        CURRENT_PASSWORD,
        is_active=True,
    )
    with freeze_time("2026-03-01 00:00:00"):
        token = create_password_reset_token(account)

    def reject_publication(*_args: object, **_kwargs: object) -> None:
        """Reject an unexpected external publication.

        Fails immediately if expired task work reaches the queue, proving lifetime calculation
        prevents a stale bearer from being published.

        Arguments:
            *_args: Ignored positional values.
            **_kwargs: Ignored keyword values.

        Returns:
            Never returns.

        Raises:
            AssertionError: Always.
        """
        message = "expired reset work reached the queue"
        raise AssertionError(message)

    monkeypatch.setattr(send_password_reset_email, "apply_async", reject_publication)
    with freeze_time("2026-03-02 00:00:01"):
        password_management_module.dispatch_password_reset_email(str(account.pk), token)


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@pytest.mark.parametrize("mutation", ["delete-account", "delete-record"])
def test_password_reset_task_revalidates_after_durable_claim(
    client: DjangoClient,
    django_user_model: type[User],
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    """Reject account or record loss between durable claim and SMTP.

    Captures queued task arguments, wraps the real durable claim to apply one competing deletion
    after commit, and verifies the second account-before-token validation prevents delivery.

    Arguments:
        client: Django test client scheduling reset work.
        django_user_model: Configured custom account model.
        monkeypatch: Fixture controlling publication and post-claim state.
        mutation: Authoritative row removed after the claim commits.

    Returns:
        None.

    Raises:
        AssertionError: If post-claim stale state can send email.
    """
    suffix = uuid.uuid4().hex
    account = django_user_model.objects.create_user(
        f"reset-post-claim-{suffix}",
        f"reset-post-claim-{suffix}@localforge.invalid",
        CURRENT_PASSWORD,
        is_active=True,
    )
    captured: list[tuple[str, str]] = []

    def capture_publication(
        *,
        args: tuple[str, str],
        expires: int,
    ) -> None:
        """Capture queued task arguments without executing delivery.

        Retains the real task call until the test installs a post-claim competing mutation, keeping
        request issuance independent of the worker race.

        Arguments:
            args: Account and token task arguments.
            expires: Positive broker expiry.

        Returns:
            None.
        """
        assert expires > 0
        captured.append(args)

    monkeypatch.setattr(send_password_reset_email, "apply_async", capture_publication)
    client.post(
        "/api/v1/users/reset_password/",
        {"email": account.email},
        content_type="application/json",
        REMOTE_ADDR="192.0.2.70",
    )
    original_claim = cast(
        "Callable[[uuid.UUID, str, str], account_tasks_module.PasswordResetDeliveryClaim | None]",
        vars(account_tasks_module)["_claim_password_reset_delivery"],
    )

    def mutate_after_claim(
        subject_id: uuid.UUID,
        digest: str,
        token: str,
    ) -> account_tasks_module.PasswordResetDeliveryClaim | None:
        """Apply one competing deletion after the real durable claim.

        Commits the production claim first, then removes the live account or token record so the
        worker's second validation must reject stale state before SMTP.

        Arguments:
            subject_id: Immutable account key.
            digest: Reset token digest.
            token: Reset bearer value.

        Returns:
            Real delivery claim, if one was authorized.
        """
        claim = original_claim(subject_id, digest, token)
        assert claim is not None
        if mutation == "delete-account":
            django_user_model.objects.using("default").filter(pk=subject_id).delete()
        else:
            PasswordResetToken.objects.using("default").filter(pk=claim.record_id).delete()
        return claim

    monkeypatch.setattr(
        account_tasks_module,
        "_claim_password_reset_delivery",
        mutate_after_claim,
    )
    delivered = send_password_reset_email(*captured[0])

    assert delivered is False
    assert not mail.outbox


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_concurrent_reset_confirm_has_one_winner_and_one_used_replay(
    client: DjangoClient,
    django_user_model: type[User],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Serialize two confirmations on the primary account row.

    Holds both requests before preflight so each observes the token unused, then releases them
    together and verifies one password change wins while the locked loser returns used.

    Arguments:
        client: Django test client requesting the reset link.
        django_user_model: Configured custom account model.
        monkeypatch: Fixture synchronizing token age checks.

    Returns:
        None.

    Raises:
        AssertionError: If both confirmations succeed or replay classification drifts.
    """
    suffix = uuid.uuid4().hex
    account = django_user_model.objects.create_user(
        f"reset-race-{suffix}",
        f"reset-race-{suffix}@localforge.invalid",
        CURRENT_PASSWORD,
        is_active=True,
    )
    link = _request_reset_link(client, account, remote_address="192.0.2.71")
    body = _confirm_payload(link)
    barrier = Barrier(2)
    call_lock = Lock()
    expiry_calls = 0
    original_expiry = password_reset_token_is_expired

    def synchronized_expiry(token: str) -> bool:
        """Synchronize both confirmation requests before token preflight.

        Holds each request at the same public token-age decision so both can observe the reset
        record before account-row locking selects a single winner.

        Arguments:
            token: Reset bearer whose age is being checked.

        Returns:
            Real expiry decision after both requests arrive.
        """
        nonlocal expiry_calls
        with call_lock:
            expiry_calls += 1
            call_number = expiry_calls
        if call_number <= PREFLIGHT_EXPIRY_CALLS:
            assert barrier.wait(timeout=10) in {0, 1}
        return original_expiry(token)

    monkeypatch.setattr(
        password_management_module,
        "password_reset_token_is_expired",
        synchronized_expiry,
    )

    def confirm(index: int) -> tuple[int, object]:
        """Submit confirmation on an independent database connection.

        Uses a separate Django client and connection per thread so PostgreSQL row locking, rather
        than one connection's transaction state, decides the race.

        Arguments:
            index: Address suffix distinguishing throttle dimensions.

        Returns:
            HTTP status and decoded body when present.
        """
        close_old_connections()
        try:
            response = DjangoClient().post(
                "/api/v1/users/reset_password_confirm/",
                body,
                content_type="application/json",
                REMOTE_ADDR=f"192.0.2.{72 + index}",
            )
            payload = response.json() if response.content else None
            return response.status_code, payload
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(confirm, range(2)))

    statuses = sorted(status for status, _payload in results)
    codes = {
        payload["code"]
        for status, payload in results
        if status == HTTPStatus.BAD_REQUEST and isinstance(payload, dict)
    }

    assert statuses == [HTTPStatus.NO_CONTENT, HTTPStatus.BAD_REQUEST]
    assert codes == {ErrorCode.PASSWORD_RESET_TOKEN_USED}


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@pytest.mark.timeout(30)
@override_settings(PASSWORD_RESET_TIMEOUT=60)
def test_reset_confirm_rechecks_expiry_after_account_lock_wait(
    client: DjangoClient,
    django_user_model: type[User],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Return expired when a valid bearer ages out while waiting for its account lock.

    Holds the account row after preflight reports a valid age, advances the frozen clock beyond
    the configured lifetime, and releases confirmation to prove locked expiry is checked before
    account binding or password replacement.

    Arguments:
        client: Django test client requesting the reset link.
        django_user_model: Configured custom account model.
        monkeypatch: Fixture observing the preflight age decision.

    Returns:
        None.

    Raises:
        AssertionError: If lock wait permits an expired bearer to change the password.
    """
    suffix = uuid.uuid4().hex
    account = django_user_model.objects.create_user(
        f"reset-expiry-lock-{suffix}",
        f"reset-expiry-lock-{suffix}@localforge.invalid",
        CURRENT_PASSWORD,
        is_active=True,
    )
    with freeze_time("2026-05-01 00:00:00") as frozen:
        link = _request_reset_link(client, account, remote_address="192.0.2.138")
        preflight_complete = Event()
        original_expiry = password_reset_token_is_expired

        def observe_expiry(token: str) -> bool:
            """Signal the first token-age decision and preserve the real result.

            Identifies the point after public preflight but before account locking so the test can
            advance time only while the request is blocked on authoritative state.

            Arguments:
                token: Reset bearer whose age is being checked.

            Returns:
                Real expiry decision at the frozen clock.
            """
            expired = original_expiry(token)
            if not preflight_complete.is_set():
                preflight_complete.set()
            return expired

        monkeypatch.setattr(
            password_management_module,
            "password_reset_token_is_expired",
            observe_expiry,
        )

        def confirm() -> tuple[int, object]:
            """Submit confirmation on an independent database connection.

            Keeps the worker blocked by the test-held account lock and returns its public status
            and body after the lock is released.

            Arguments:
                None.

            Returns:
                HTTP status and decoded response body.
            """
            close_old_connections()
            try:
                response = DjangoClient().post(
                    "/api/v1/users/reset_password_confirm/",
                    _confirm_payload(link),
                    content_type="application/json",
                    REMOTE_ADDR="192.0.2.139",
                )
                return response.status_code, response.json()
            finally:
                close_old_connections()

        executor = ThreadPoolExecutor(max_workers=1)
        try:
            with transaction.atomic(using="default"):
                django_user_model.objects.using("default").select_for_update().get(pk=account.pk)
                future = executor.submit(confirm)
                assert preflight_complete.wait(timeout=10)
                frozen.tick(delta=timedelta(seconds=61))
            status, payload = future.result(timeout=10)
        finally:
            executor.shutdown(wait=True)

    account.refresh_from_db(using="default")
    record = PasswordResetToken.objects.using("default").get(account=account)

    assert status == HTTPStatus.BAD_REQUEST
    assert cast("dict[str, Any]", payload)["code"] == ErrorCode.PASSWORD_RESET_TOKEN_EXPIRED
    assert account.check_password(CURRENT_PASSWORD)
    assert record.used_at is None


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_password_change_handles_account_disappearance_and_database_failure(
    client: DjangoClient,
    django_user_model: type[User],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Contain account loss after authentication and credential-store failure.

    Deletes one account after serializer validation and makes another credential revocation raise,
    proving both races return the shared authenticated or dependency envelope through HTTP.

    Arguments:
        client: Django test client issuing password changes.
        django_user_model: Configured custom account model.
        monkeypatch: Fixture inducing post-authentication races.

    Returns:
        None.

    Raises:
        AssertionError: If either race escapes the API boundary.
    """
    first = django_user_model.objects.create_user(
        f"password-disappear-{uuid.uuid4().hex}",
        f"password-disappear-{uuid.uuid4().hex}@localforge.invalid",
        CURRENT_PASSWORD,
        is_active=True,
    )
    first_token = Token.objects.using("default").create(user=first)
    original_validation = password_management_module.PasswordChangeSerializer.is_valid

    def delete_after_validation(
        serializer: password_management_module.PasswordChangeSerializer,
        *,
        raise_exception: bool = False,
    ) -> bool:
        """Delete the authenticated account after body validation.

        Models account deletion after request authentication but before the password transaction
        reacquires the authoritative primary row.

        Arguments:
            serializer: Password-change serializer being validated.
            raise_exception: Whether invalid input should raise.

        Returns:
            Real serializer validation result.
        """
        result = cast("bool", original_validation(serializer, raise_exception=raise_exception))
        django_user_model.objects.using("default").filter(pk=first.pk).delete()
        return result

    monkeypatch.setattr(
        password_management_module.PasswordChangeSerializer,
        "is_valid",
        delete_after_validation,
    )
    disappeared = client.post(
        "/api/v1/users/set_password/",
        {
            "current_password": CURRENT_PASSWORD,
            "new_password": NEW_PASSWORD,
            "new_password_confirm": NEW_PASSWORD,
        },
        content_type="application/json",
        headers={"authorization": f"Token {first_token.key}"},
    )

    second = django_user_model.objects.create_user(
        f"password-database-{uuid.uuid4().hex}",
        f"password-database-{uuid.uuid4().hex}@localforge.invalid",
        CURRENT_PASSWORD,
        is_active=True,
    )
    second_token = Token.objects.using("default").create(user=second)
    monkeypatch.setattr(
        password_management_module.PasswordChangeSerializer,
        "is_valid",
        original_validation,
    )

    def fail_revocation(_account: User) -> None:
        """Raise one credential-store database failure.

        Models an authoritative credential write failure after password hashing so the surrounding
        transaction must roll back and return the dependency envelope.

        Arguments:
            _account: Account whose credentials would be revoked.

        Returns:
            Never returns.

        Raises:
            DatabaseError: Always.
        """
        raise DatabaseError

    monkeypatch.setattr(
        password_management_module,
        "revoke_account_credentials",
        fail_revocation,
    )
    unavailable = client.post(
        "/api/v1/users/set_password/",
        {
            "current_password": CURRENT_PASSWORD,
            "new_password": NEW_PASSWORD,
            "new_password_confirm": NEW_PASSWORD,
        },
        content_type="application/json",
        headers={"authorization": f"Token {second_token.key}"},
    )

    assert disappeared.status_code == HTTPStatus.UNAUTHORIZED
    assert unavailable.status_code == HTTPStatus.SERVICE_UNAVAILABLE


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_password_reset_confirm_contains_locked_validation_and_database_races(
    client: DjangoClient,
    django_user_model: type[User],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Contain validator drift and database loss after reset preflight.

    Makes account-aware validation pass before admission but fail under the primary lock, then
    induces a database failure after another token preflight and verifies both documented envelopes.

    Arguments:
        client: Django test client issuing reset confirmations.
        django_user_model: Configured custom account model.
        monkeypatch: Fixture inducing locked validation and persistence failures.

    Returns:
        None.

    Raises:
        AssertionError: If either post-admission race escapes the HTTP boundary.
    """
    first = django_user_model.objects.create_user(
        f"reset-validator-race-{uuid.uuid4().hex}",
        f"reset-validator-race-{uuid.uuid4().hex}@localforge.invalid",
        CURRENT_PASSWORD,
        is_active=True,
    )
    first_link = _request_reset_link(client, first, remote_address="192.0.2.74")
    original_validate = django_validate_password
    calls = 0

    def fail_second_validation(password: str, user: User | None = None) -> None:
        """Fail only the locked validation pass.

        Allows pre-admission validation to succeed, then models account-policy drift before the
        transaction revalidates against the locked primary row.

        Arguments:
            password: Replacement password being validated.
            user: Account attributes supplied to configured validators.

        Returns:
            None on the pre-admission pass.

        Raises:
            ValidationError: On the locked transaction pass.
        """
        nonlocal calls
        calls += 1
        if calls == LOCKED_VALIDATION_CALL:
            message = "Locked policy changed."
            raise DjangoValidationError(message)
        original_validate(password, user=user)

    monkeypatch.setattr(
        password_management_module,
        "validate_password",
        fail_second_validation,
    )
    validation = client.post(
        "/api/v1/users/reset_password_confirm/",
        _confirm_payload(first_link),
        content_type="application/json",
        REMOTE_ADDR="192.0.2.75",
    )

    second = django_user_model.objects.create_user(
        f"reset-database-race-{uuid.uuid4().hex}",
        f"reset-database-race-{uuid.uuid4().hex}@localforge.invalid",
        CURRENT_PASSWORD,
        is_active=True,
    )
    monkeypatch.setattr(password_management_module, "validate_password", original_validate)
    second_link = _request_reset_link(client, second, remote_address="192.0.2.76")

    def fail_locked_reset(*_args: object, **_kwargs: object) -> None:
        """Raise one post-preflight database failure.

        Models loss of authoritative state after token classification so the outer recovery
        boundary must normalize the database exception to service unavailable.

        Arguments:
            *_args: Ignored positional values.
            **_kwargs: Ignored keyword values.

        Returns:
            Never returns.

        Raises:
            DatabaseError: Always.
        """
        raise DatabaseError

    monkeypatch.setattr(
        password_management_module,
        "_apply_locked_password_reset",
        fail_locked_reset,
    )
    unavailable = client.post(
        "/api/v1/users/reset_password_confirm/",
        _confirm_payload(second_link),
        content_type="application/json",
        REMOTE_ADDR="192.0.2.77",
    )

    assert validation.status_code == HTTPStatus.BAD_REQUEST
    assert validation.json()["details"]["new_password"] == ["Locked policy changed."]
    assert unavailable.status_code == HTTPStatus.SERVICE_UNAVAILABLE


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_reset_record_deletion_after_preflight_returns_foreign(
    client: DjangoClient,
    django_user_model: type[User],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject a reset record removed between preflight and account locking.

    Wraps the real locked transition to delete only the preflighted token record first, proving a
    concurrent cleanup or administrative deletion returns foreign rather than changing a password.

    Arguments:
        client: Django test client issuing reset confirmation.
        django_user_model: Configured custom account model.
        monkeypatch: Fixture applying the competing token-record deletion.

    Returns:
        None.

    Raises:
        AssertionError: If missing authoritative token state changes the password or escapes.
    """
    suffix = uuid.uuid4().hex
    account = django_user_model.objects.create_user(
        f"reset-record-race-{suffix}",
        f"reset-record-race-{suffix}@localforge.invalid",
        CURRENT_PASSWORD,
        is_active=True,
    )
    link = _request_reset_link(client, account, remote_address="192.0.2.78")
    original_apply = cast(
        "Callable[[uuid.UUID, int, str, str, str], None]",
        vars(password_management_module)["_apply_locked_password_reset"],
    )

    def delete_then_apply(
        account_id: uuid.UUID,
        record_id: int,
        digest: str,
        token: str,
        new_password: str,
    ) -> None:
        """Delete the preflighted record before the real locked transition.

        Preserves every production argument while introducing only the concurrent authoritative
        record loss whose public classification is under test.

        Arguments:
            account_id: Immutable account key.
            record_id: Preflighted reset record key.
            digest: Submitted bearer digest.
            token: Submitted reset bearer.
            new_password: Validated replacement password.

        Returns:
            None.

        Raises:
            PasswordResetTokenForeign: From the real transition after deletion.
        """
        PasswordResetToken.objects.using("default").filter(pk=record_id).delete()
        original_apply(account_id, record_id, digest, token, new_password)

    monkeypatch.setattr(
        password_management_module,
        "_apply_locked_password_reset",
        delete_then_apply,
    )
    response = client.post(
        "/api/v1/users/reset_password_confirm/",
        _confirm_payload(link),
        content_type="application/json",
        REMOTE_ADDR="192.0.2.79",
    )
    account.refresh_from_db(using="default")

    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert response.json()["code"] == ErrorCode.PASSWORD_RESET_TOKEN_FOREIGN
    assert account.check_password(CURRENT_PASSWORD)


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_reset_consume_then_account_delete_after_preflight_returns_used(
    client: DjangoClient,
    django_user_model: type[User],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Preserve used precedence when consumption and deletion win after preflight.

    Mutates the preflighted record and removes its live account before the real locked transition,
    proving immutable token state remains authoritative after the nullable relationship is cleared.

    Arguments:
        client: Django test client issuing reset confirmation.
        django_user_model: Configured custom account model.
        monkeypatch: Fixture applying the competing consume-and-delete transaction.

    Returns:
        None.

    Raises:
        AssertionError: If missing live account state masks committed token consumption.
    """
    suffix = uuid.uuid4().hex
    account = django_user_model.objects.create_user(
        f"reset-consume-delete-race-{suffix}",
        f"reset-consume-delete-race-{suffix}@localforge.invalid",
        CURRENT_PASSWORD,
        is_active=True,
    )
    link = _request_reset_link(client, account, remote_address="192.0.2.180")
    account_id = account.pk
    original_apply = cast(
        "Callable[[uuid.UUID, int, str, str, str], None]",
        vars(password_management_module)["_apply_locked_password_reset"],
    )

    def consume_delete_then_apply(
        submitted_account_id: uuid.UUID,
        record_id: int,
        digest: str,
        token: str,
        new_password: str,
    ) -> None:
        """Commit competing token consumption and account deletion before locked classification.

        Applies both mutations in one committed primary transaction, then invokes production
        classification against the resulting immutable tombstone state.

        Arguments:
            submitted_account_id: Immutable account key from the confirmation body.
            record_id: Preflighted reset record key.
            digest: Submitted bearer digest.
            token: Submitted reset bearer.
            new_password: Validated replacement password.

        Returns:
            None.

        Raises:
            PasswordResetTokenUsed: From the real locked transition.
        """
        with transaction.atomic(using="default"):
            PasswordResetToken.objects.using("default").filter(pk=record_id).update(
                used_at=timezone.now()
            )
            django_user_model.objects.using("default").filter(pk=submitted_account_id).delete()
        original_apply(submitted_account_id, record_id, digest, token, new_password)

    monkeypatch.setattr(
        password_management_module,
        "_apply_locked_password_reset",
        consume_delete_then_apply,
    )
    response = client.post(
        "/api/v1/users/reset_password_confirm/",
        _confirm_payload(link),
        content_type="application/json",
        REMOTE_ADDR="192.0.2.181",
    )

    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert response.json()["code"] == ErrorCode.PASSWORD_RESET_TOKEN_USED
    record = PasswordResetToken.objects.using("default").get(pk__isnull=False)
    assert record.subject_id == account_id
    assert record.account is None
    assert record.used_at is not None


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_password_flows_preserve_reset_required_hash_policy(
    client: DjangoClient,
    django_user_model: type[User],
) -> None:
    """Refuse authenticated verification but permit recovery of an unsafe stored profile.

    Gives one account a retired encoding after issuing a secondary token, proving set-password
    never evaluates it while email recovery replaces it with the configured preferred hasher.

    Arguments:
        client: Django test client issuing both password flows.
        django_user_model: Configured custom account model.

    Returns:
        None.

    Raises:
        AssertionError: If authenticated change verifies the unsafe hash or recovery cannot
            repair it.
    """
    suffix = uuid.uuid4().hex
    account = django_user_model.objects.create_user(
        f"reset-required-{suffix}",
        f"reset-required-{suffix}@localforge.invalid",
        CURRENT_PASSWORD,
        is_active=True,
    )
    token = Token.objects.using("default").create(user=account)
    account.password = "retired_hasher$1$salt$digest"  # noqa: S105
    account.save(using="default", update_fields=("password",))

    changed = client.post(
        "/api/v1/users/set_password/",
        {
            "current_password": CURRENT_PASSWORD,
            "new_password": NEW_PASSWORD,
            "new_password_confirm": NEW_PASSWORD,
        },
        content_type="application/json",
        headers={"authorization": f"Token {token.key}"},
    )
    link = _request_reset_link(client, account, remote_address="192.0.2.82")
    recovered = client.post(
        "/api/v1/users/reset_password_confirm/",
        _confirm_payload(link),
        content_type="application/json",
        REMOTE_ADDR="192.0.2.83",
    )
    account.refresh_from_db(using="default")

    assert changed.status_code == HTTPStatus.BAD_REQUEST
    assert changed.json()["details"]["current_password"] == ["The current password is incorrect."]
    assert recovered.status_code == HTTPStatus.NO_CONTENT
    assert account.check_password(NEW_PASSWORD)
    assert account.password.startswith("argon2$")


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@override_settings(
    PASSWORD_RESET_ADDRESS_THROTTLE_RATE=HIGH_RESET_RATE,
    PASSWORD_RESET_ACCOUNT_THROTTLE_RATE=LOW_RESET_RATE,
    PASSWORD_RESET_MINIMUM_RESPONSE_DURATION_SECONDS=0.001,
)
def test_reset_recipient_dimensions_survive_account_creation_and_email_change(
    client: DjangoClient,
    django_user_model: type[User],
) -> None:
    """Keep stable email and immutable account reset limits across identity transitions.

    Charges an unknown email before account creation and an account before its email changes,
    proving neither transition creates a fresh authoritative recipient quota.

    Arguments:
        client: Django test client issuing reset requests.
        django_user_model: Configured custom account model.

    Returns:
        None.

    Raises:
        AssertionError: If account creation or email replacement resets recipient admission.
    """
    suffix = uuid.uuid4().hex
    future_email = f"future-reset-{suffix}@localforge.invalid"
    unknown = client.post(
        "/api/v1/users/reset_password/",
        {"email": future_email},
        content_type="application/json",
        REMOTE_ADDR="192.0.2.84",
    )
    created = django_user_model.objects.create_user(
        f"future-reset-{suffix}",
        future_email,
        CURRENT_PASSWORD,
        is_active=True,
    )
    after_creation = client.post(
        "/api/v1/users/reset_password/",
        {"email": created.email},
        content_type="application/json",
        REMOTE_ADDR="192.0.2.85",
    )

    account = django_user_model.objects.create_user(
        f"changed-reset-{suffix}",
        f"changed-reset-{suffix}@localforge.invalid",
        CURRENT_PASSWORD,
        is_active=True,
    )
    before_change = client.post(
        "/api/v1/users/reset_password/",
        {"email": account.email},
        content_type="application/json",
        REMOTE_ADDR="192.0.2.86",
    )
    account.email = f"replacement-reset-{suffix}@localforge.invalid"
    account.save(using="default", update_fields=("email", "updated_at"))
    after_change = client.post(
        "/api/v1/users/reset_password/",
        {"email": account.email},
        content_type="application/json",
        REMOTE_ADDR="192.0.2.87",
    )

    assert unknown.status_code == HTTPStatus.ACCEPTED
    assert after_creation.status_code == HTTPStatus.TOO_MANY_REQUESTS
    assert before_change.status_code == HTTPStatus.ACCEPTED
    assert after_change.status_code == HTTPStatus.TOO_MANY_REQUESTS


@pytest.mark.integration
@pytest.mark.services("postgres", "mailpit", "valkey-cache")
@pytest.mark.serial
@pytest.mark.timeout(MAILPIT_TIMEOUT_SECONDS)
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@pytest.mark.skipif(
    settings.EMAIL_BACKEND != SMTP_BACKEND,
    reason="the real password recovery loop runs only under the Compose smtp profile",
)
@override_settings(EMAIL_BACKEND=SMTP_BACKEND)
def test_password_recovery_round_trips_through_smtp_and_mailpit(
    client: DjangoClient,
    django_user_model: type[User],
) -> None:
    """Complete the password recovery loop through real SMTP capture.

    Clears the persistent capture service, requests and reads the reset email through Mailpit,
    confirms the extracted link, and observes the password-change notification through Mailpit.
    This test is serial because clearing one shared capture service cannot be worker-namespaced.

    Arguments:
        client: Django test client issuing the full recovery loop.
        django_user_model: Configured custom account model.

    Returns:
        None.

    Raises:
        AssertionError: If either multipart message or the recovered password is missing.
    """
    _mailpit_request("DELETE", "/api/v1/messages")
    suffix = uuid.uuid4().hex
    account = django_user_model.objects.create_user(
        f"smtp-reset-{suffix}",
        f"smtp-reset-{suffix}@localforge.invalid",
        CURRENT_PASSWORD,
        is_active=True,
    )
    requested = client.post(
        "/api/v1/users/reset_password/",
        {"email": account.email},
        content_type="application/json",
        REMOTE_ADDR="192.0.2.80",
    )
    assert requested.status_code == HTTPStatus.ACCEPTED

    deadline = time.monotonic() + MAILPIT_TIMEOUT_SECONDS
    reset_text = ""
    while time.monotonic() < deadline:
        listing = cast("dict[str, Any]", _mailpit_request("GET", "/api/v1/messages"))
        messages = cast("list[dict[str, Any]]", listing["messages"])
        reset_message = next(
            (
                message
                for message in messages
                if message["Subject"] == "Reset your LocalForge password"
            ),
            None,
        )
        if reset_message is not None:
            detail = cast(
                "dict[str, Any]",
                _mailpit_request("GET", f"/api/v1/message/{reset_message['ID']}"),
            )
            reset_text = cast("str", detail["Text"])
            assert cast("str", detail["HTML"]).strip()
            break
        time.sleep(MAILPIT_POLL_SECONDS)
    assert reset_text
    query = parse_qs(urlparse(reset_text.split("Continue at ", maxsplit=1)[1].strip()).query)

    confirmed = client.post(
        "/api/v1/users/reset_password_confirm/",
        _confirm_payload({"account": query["account"][0], "token": query["token"][0]}),
        content_type="application/json",
        REMOTE_ADDR="192.0.2.81",
    )
    assert confirmed.status_code == HTTPStatus.NO_CONTENT

    while time.monotonic() < deadline:
        listing = cast("dict[str, Any]", _mailpit_request("GET", "/api/v1/messages"))
        subjects = {
            cast("str", message["Subject"])
            for message in cast("list[dict[str, Any]]", listing["messages"])
        }
        if "Your LocalForge password changed" in subjects:
            account.refresh_from_db(using="default")
            assert account.check_password(NEW_PASSWORD)
            return
        time.sleep(MAILPIT_POLL_SECONDS)

    pytest.fail("Mailpit did not capture the password-change notification before timeout")
