"""Integration tests for username management.

Exercises the three fixed username HTTP boundaries and captured-email seam against authoritative
account, credential, token, and uniqueness state.
"""

import http.client
import json
import logging
import statistics
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from http import HTTPStatus
from threading import Barrier
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import parse_qs, urlparse

import pytest
from django.conf import settings
from django.core import mail
from django.db import DatabaseError, IntegrityError, close_old_connections
from django.db.models import QuerySet
from django.test import Client as DjangoClient
from django.test import override_settings
from django.utils import timezone
from drf_spectacular.generators import SchemaGenerator
from freezegun import freeze_time
from rest_framework.authtoken.models import Token
from rest_framework.exceptions import AuthenticationFailed

import accounts.tasks as account_tasks_module
import accounts.username_management as username_management_module
from accounts.jwt_authentication import PrimaryRefreshToken
from accounts.login_throttle import PostgresLoginThrottleStore
from accounts.models import LoginThrottleEvent, UsernameResetToken
from accounts.tasks import deliver_username_reset_email, send_username_reset_email
from accounts.username_management import change_username, dispatch_username_reset_email
from accounts.username_tokens import (
    create_username_reset_token,
    username_reset_token_generator,
    username_reset_token_remaining_seconds,
    username_reset_token_timestamp,
)
from config.api_errors import ErrorCode
from config.logs import StructuredFormatter

if TYPE_CHECKING:
    from collections.abc import Callable

    from django.core.mail import EmailMultiAlternatives

    from accounts.models import User

CURRENT_PASSWORD = "Correct-Horse-Battery-Staple-34"  # noqa: S105
RESET_ACCEPTED_MESSAGE = "If an account matches this address, a username reset email will be sent."
HIGH_RESET_RATE = "1000/hour"
LOW_RESET_RATE = "1/hour"
TIMING_WARMUP_REQUESTS = 5
TIMING_MEASURED_REQUESTS = 30
TIMING_RELATIVE_LIMIT = 0.20
TIMING_ABSOLUTE_LIMIT_SECONDS = 0.010
SMTP_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
MAILPIT_TIMEOUT_SECONDS = 15
MAILPIT_POLL_SECONDS = 0.1
RACE_WAIT_SECONDS = 45
EXPECTED_NOTIFICATION_MESSAGE_COUNT = 2
EXPECTED_DIVERGENT_TIMEOUT_MESSAGE_COUNT = 3
EXPECTED_TASK_ARGUMENT_COUNT = 2
SECOND_ACCOUNT_LOOKUP = 2
timing_logger = logging.getLogger("localforge.tests.username_reset_timing")
pytestmark = pytest.mark.xdist_group(name="username-management")


def _request_reset_link(
    client: DjangoClient,
    account: User,
    *,
    remote_address: str,
) -> dict[str, str]:
    """Request and extract one captured username-reset link.

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
        "/api/v1/users/reset_username/",
        {"email": account.email},
        content_type="application/json",
        REMOTE_ADDR=remote_address,
    )
    assert response.status_code == HTTPStatus.ACCEPTED
    message = cast("EmailMultiAlternatives", mail.outbox[-1])
    query = parse_qs(urlparse(message.body.split("Continue at ", maxsplit=1)[1].strip()).query)
    assert set(query) == {"account", "token"}
    return {"account": query["account"][0], "token": query["token"][0]}


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


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_authenticated_username_change_preserves_existing_credentials(
    client: DjangoClient,
    django_user_model: type[User],
) -> None:
    """Change the caller username while every existing credential remains valid.

    Authenticates with the secondary token, retains a JSON web token pair, and proves both old
    credentials still resolve the renamed account after the current password authorizes the change.

    Arguments:
        client: Django test client issuing the username change.
        django_user_model: Configured custom account model.

    Returns:
        None.

    Raises:
        AssertionError: If the change fails, mail is absent, or a credential is invalidated.
    """
    account = django_user_model.objects.create_user(
        "username-before",
        "username-before@localforge.invalid",
        CURRENT_PASSWORD,
        is_active=True,
    )
    secondary = Token.objects.using("default").create(user=account)
    refresh = PrimaryRefreshToken.for_user(account)
    access = str(refresh.access_token)

    response = client.post(
        "/api/v1/users/set_username/",
        {"current_password": CURRENT_PASSWORD, "new_username": "username-after"},
        content_type="application/json",
        headers={"authorization": f"Token {secondary.key}"},
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
    account.refresh_from_db(using="default")

    assert response.status_code == HTTPStatus.NO_CONTENT
    assert account.username == "username-after"
    assert token_probe.status_code == HTTPStatus.OK
    assert access_probe.status_code == HTTPStatus.OK
    assert refresh_probe.status_code == HTTPStatus.OK
    assert len(mail.outbox) == 1
    message = cast("EmailMultiAlternatives", mail.outbox[0])
    assert message.to == [account.email]
    assert message.subject == "Your LocalForge username changed"
    assert "username-before" not in message.body
    assert "username-after" not in message.body


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_authenticated_username_change_contains_notification_failure(
    client: DjangoClient,
    django_user_model: type[User],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Return the committed change when notification publication raises.

    Forces the after-commit notification boundary to raise unsafe diagnostics, then verifies the
    endpoint returns success and emits one correlated redacted operational record.

    Arguments:
        client: Django test client issuing the authenticated change.
        django_user_model: Configured custom account model.
        monkeypatch: Fixture replacing notification publication.
        caplog: Fixture collecting the operational failure record.

    Returns:
        None.

    Raises:
        AssertionError: If the failure escapes, state rolls back, or sensitive data is logged.
    """
    old_username = "username-notification-before"
    new_username = "username-notification-after"
    account = django_user_model.objects.create_user(
        old_username,
        "username-notification@localforge.invalid",
        CURRENT_PASSWORD,
        is_active=True,
    )
    token = Token.objects.using("default").create(user=account)

    def fail_notification(email: str) -> None:
        """Raise unsafe notification diagnostics.

        Models an unexpected publication failure containing every protected value class so the
        endpoint's operational logging boundary proves it records only the exception type.

        Arguments:
            email: Account address passed to notification delivery.

        Returns:
            Never returns.

        Raises:
            RuntimeError: Always.
        """
        message = f"{old_username} {new_username} {email} {token.key}"
        raise RuntimeError(message)

    monkeypatch.setattr(username_management_module, "notify_username_changed", fail_notification)
    with caplog.at_level(logging.ERROR, logger=username_management_module.__name__):
        response = client.post(
            "/api/v1/users/set_username/",
            {"current_password": CURRENT_PASSWORD, "new_username": new_username},
            content_type="application/json",
            headers={"authorization": f"Token {token.key}"},
        )
    account.refresh_from_db(using="default")
    failure_records = [
        record
        for record in caplog.records
        if record.getMessage() == "Username change notification failed"
    ]

    assert response.status_code == HTTPStatus.NO_CONTENT
    assert account.username == new_username
    assert len(failure_records) == 1
    failure_record = failure_records[0]
    rendered = StructuredFormatter().format(failure_record)
    assert vars(failure_record)["operation_error_type"] == "RuntimeError"
    assert vars(failure_record)["request_id"] == response.headers["X-Request-ID"]
    assert old_username not in rendered
    assert new_username not in rendered
    assert account.email not in rendered
    assert token.key not in rendered


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_username_reset_full_loop_is_single_use_and_preserves_credentials(
    client: DjangoClient,
    django_user_model: type[User],
) -> None:
    """Complete username recovery once while preserving pre-change credentials.

    Requests the account-bound link through email, applies a unique replacement, observes the
    notification, and proves replay is used while secondary and JSON web tokens remain valid.

    Arguments:
        client: Django test client issuing the recovery loop.
        django_user_model: Configured custom account model.

    Returns:
        None.

    Raises:
        AssertionError: If recovery, replay classification, mail, or credentials differ.
    """
    suffix = uuid.uuid4().hex
    account = django_user_model.objects.create_user(
        f"username-reset-before-{suffix}",
        f"username-reset-{suffix}@localforge.invalid",
        CURRENT_PASSWORD,
        is_active=True,
    )
    secondary = Token.objects.using("default").create(user=account)
    refresh = PrimaryRefreshToken.for_user(account)
    access = str(refresh.access_token)

    requested = client.post(
        "/api/v1/users/reset_username/",
        {"email": account.email},
        content_type="application/json",
        REMOTE_ADDR="192.0.2.201",
    )
    message = cast("EmailMultiAlternatives", mail.outbox[0])
    query = parse_qs(urlparse(message.body.split("Continue at ", maxsplit=1)[1].strip()).query)
    payload = {
        "account": query["account"][0],
        "token": query["token"][0],
        "new_username": f"username-reset-after-{suffix}",
    }
    confirmed = client.post(
        "/api/v1/users/reset_username_confirm/",
        payload,
        content_type="application/json",
        REMOTE_ADDR="192.0.2.202",
    )
    replayed = client.post(
        "/api/v1/users/reset_username_confirm/",
        payload,
        content_type="application/json",
        REMOTE_ADDR="192.0.2.203",
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
    account.refresh_from_db(using="default")

    assert requested.status_code == HTTPStatus.ACCEPTED
    assert requested.json() == {
        "detail": "If an account matches this address, a username reset email will be sent."
    }
    assert message.to == [account.email]
    assert message.subject == "Reset your LocalForge username"
    assert confirmed.status_code == HTTPStatus.NO_CONTENT
    assert account.username == payload["new_username"]
    record = UsernameResetToken.objects.using("default").get(subject_id=account.pk)
    assert str(record) == f"{account.pk}:{record.pk}"
    assert replayed.status_code == HTTPStatus.BAD_REQUEST
    assert replayed.json()["code"] == ErrorCode.USERNAME_RESET_TOKEN_USED
    assert token_probe.status_code == HTTPStatus.OK
    assert access_probe.status_code == HTTPStatus.OK
    assert refresh_probe.status_code == HTTPStatus.OK
    assert len(mail.outbox) == EXPECTED_NOTIFICATION_MESSAGE_COUNT
    assert mail.outbox[1].subject == "Your LocalForge username changed"


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_username_reset_confirm_contains_notification_failure(
    client: DjangoClient,
    django_user_model: type[User],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Return the committed reset when notification publication raises.

    Obtains a valid bearer through the public reset seam, forces its post-commit notification to
    raise unsafe diagnostics, and verifies correlated redacted success.

    Arguments:
        client: Django test client issuing reset and confirmation requests.
        django_user_model: Configured custom account model.
        monkeypatch: Fixture replacing notification publication.
        caplog: Fixture collecting the operational failure record.

    Returns:
        None.

    Raises:
        AssertionError: If the failure escapes, state rolls back, or sensitive data is logged.
    """
    old_username = "username-confirm-notification-before"
    new_username = "username-confirm-notification-after"
    account = django_user_model.objects.create_user(
        old_username,
        "username-confirm-notification@localforge.invalid",
        CURRENT_PASSWORD,
        is_active=True,
    )
    link = _request_reset_link(client, account, remote_address="192.0.2.249")

    def fail_notification(email: str) -> None:
        """Raise unsafe reset-notification diagnostics.

        Models unexpected publication failure after username and bearer state commit while placing
        all protected values in the exception text.

        Arguments:
            email: Account address passed to notification delivery.

        Returns:
            Never returns.

        Raises:
            RuntimeError: Always.
        """
        message = f"{old_username} {new_username} {email} {link['token']}"
        raise RuntimeError(message)

    monkeypatch.setattr(username_management_module, "notify_username_changed", fail_notification)
    with caplog.at_level(logging.ERROR, logger=username_management_module.__name__):
        response = client.post(
            "/api/v1/users/reset_username_confirm/",
            {**link, "new_username": new_username},
            content_type="application/json",
            REMOTE_ADDR="192.0.2.250",
        )
    account.refresh_from_db(using="default")
    record = UsernameResetToken.objects.using("default").get(subject_id=account.pk)
    failure_records = [
        log_record
        for log_record in caplog.records
        if log_record.getMessage() == "Username change notification failed"
    ]

    assert response.status_code == HTTPStatus.NO_CONTENT
    assert account.username == new_username
    assert record.used_at is not None
    assert len(failure_records) == 1
    failure_record = failure_records[0]
    rendered = StructuredFormatter().format(failure_record)
    assert vars(failure_record)["operation_error_type"] == "RuntimeError"
    assert vars(failure_record)["request_id"] == response.headers["X-Request-ID"]
    assert old_username not in rendered
    assert new_username not in rendered
    assert account.email not in rendered
    assert link["token"] not in rendered


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_username_changes_reject_invalid_or_unavailable_names(
    client: DjangoClient,
    django_user_model: type[User],
) -> None:
    """Reject malformed and PostgreSQL-case-insensitive occupied usernames.

    Exercises both authenticated and recovery changes with neutral field errors so an unavailable
    value reveals neither the owning account nor a database exception.

    Arguments:
        client: Django test client issuing username changes.
        django_user_model: Configured custom account model.

    Returns:
        None.

    Raises:
        AssertionError: If format or case-insensitive uniqueness is not enforced per field.
    """
    owner = django_user_model.objects.create_user(
        "OccupiedName",
        "username-owner@localforge.invalid",
        CURRENT_PASSWORD,
        is_active=True,
    )
    account = django_user_model.objects.create_user(
        "username-candidate",
        "username-candidate@localforge.invalid",
        CURRENT_PASSWORD,
        is_active=True,
    )
    token = Token.objects.using("default").create(user=account)

    malformed = client.post(
        "/api/v1/users/set_username/",
        {"current_password": CURRENT_PASSWORD, "new_username": "not allowed"},
        content_type="application/json",
        headers={"authorization": f"Token {token.key}"},
    )
    occupied = client.post(
        "/api/v1/users/set_username/",
        {"current_password": CURRENT_PASSWORD, "new_username": owner.username.lower()},
        content_type="application/json",
        headers={"authorization": f"Token {token.key}"},
    )

    assert malformed.status_code == HTTPStatus.BAD_REQUEST
    assert malformed.json()["code"] == ErrorCode.VALIDATION_ERROR
    assert "new_username" in malformed.json()["details"]
    assert occupied.status_code == HTTPStatus.BAD_REQUEST
    assert occupied.json()["details"] == {"new_username": ["That username is not available."]}


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_username_change_requires_authentication_current_password_and_exact_body(
    client: DjangoClient,
    django_user_model: type[User],
) -> None:
    """Reject unauthenticated, incorrectly authorized, and undeclared username inputs.

    Exercises the authenticated route entirely through HTTP and proves every rejected body leaves
    the account identifier unchanged and produces the shared per-field envelope.

    Arguments:
        client: Django test client issuing username changes.
        django_user_model: Configured custom account model.

    Returns:
        None.

    Raises:
        AssertionError: If authentication, password confirmation, or exact shape is bypassed.
    """
    account = django_user_model.objects.create_user(
        "username-exact",
        "username-exact@localforge.invalid",
        CURRENT_PASSWORD,
        is_active=True,
    )
    token = Token.objects.using("default").create(user=account)
    route = "/api/v1/users/set_username/"
    body = {"current_password": CURRENT_PASSWORD, "new_username": "username-exact-after"}

    unauthenticated = client.post(route, body, content_type="application/json")
    wrong_password = client.post(
        route,
        {**body, "current_password": "wrong-password"},
        content_type="application/json",
        headers={"authorization": f"Token {token.key}"},
    )
    extra = client.post(
        route,
        {**body, "account": str(account.pk)},
        content_type="application/json",
        headers={"authorization": f"Token {token.key}"},
    )
    missing = client.post(
        route,
        {"current_password": CURRENT_PASSWORD},
        content_type="application/json",
        headers={"authorization": f"Token {token.key}"},
    )
    non_object = client.post(
        route,
        data="[]",
        content_type="application/json",
        headers={"authorization": f"Token {token.key}"},
    )
    account.refresh_from_db(using="default")

    assert unauthenticated.status_code == HTTPStatus.UNAUTHORIZED
    assert wrong_password.json()["details"] == {
        "current_password": ["The current password is incorrect."]
    }
    assert extra.json()["details"] == {"account": ["This field is not allowed."]}
    assert missing.json()["details"] == {"new_username": ["This field is required."]}
    assert non_object.status_code == HTTPStatus.BAD_REQUEST
    assert account.username == "username-exact"
    assert not mail.outbox


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_username_reset_request_is_indistinguishable_across_account_states(
    client: DjangoClient,
    django_user_model: type[User],
) -> None:
    """Accept active, inactive, and unknown addresses identically.

    Posts every account-state outcome through the public boundary and proves only the active
    account receives a multipart account-bound link while every public response remains identical.

    Arguments:
        client: Django test client issuing username-reset requests.
        django_user_model: Configured custom account model.

    Returns:
        None.

    Raises:
        AssertionError: If an account state changes status, body, or delivery behavior.
    """
    suffix = uuid.uuid4().hex
    active = django_user_model.objects.create_user(
        f"username-active-{suffix}",
        f"username-active-{suffix}@localforge.invalid",
        CURRENT_PASSWORD,
        is_active=True,
    )
    inactive = django_user_model.objects.create_user(
        f"username-inactive-{suffix}",
        f"username-inactive-{suffix}@localforge.invalid",
        CURRENT_PASSWORD,
    )
    responses = [
        client.post(
            "/api/v1/users/reset_username/",
            {"email": email},
            content_type="application/json",
            REMOTE_ADDR=address,
        )
        for email, address in (
            (active.email, "192.0.2.204"),
            (inactive.email, "192.0.2.205"),
            (f"username-unknown-{suffix}@localforge.invalid", "192.0.2.206"),
        )
    ]

    assert [response.status_code for response in responses] == [HTTPStatus.ACCEPTED] * 3
    assert {json.dumps(response.json(), sort_keys=True) for response in responses} == {
        json.dumps({"detail": RESET_ACCEPTED_MESSAGE}, sort_keys=True)
    }
    assert len(mail.outbox) == 1
    message = cast("EmailMultiAlternatives", mail.outbox[0])
    assert message.to == [active.email]
    assert message.alternatives
    assert str(active.pk) in message.body
    assert active.username not in message.body


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@pytest.mark.parametrize(
    ("kind", "expected_code"),
    [
        pytest.param("malformed", ErrorCode.USERNAME_RESET_TOKEN_MALFORMED, id="malformed"),
        pytest.param("foreign", ErrorCode.USERNAME_RESET_TOKEN_FOREIGN, id="foreign"),
        pytest.param("expired", ErrorCode.USERNAME_RESET_TOKEN_EXPIRED, id="expired"),
    ],
)
def test_username_reset_confirm_classifies_invalid_tokens(
    client: DjangoClient,
    django_user_model: type[User],
    kind: str,
    expected_code: ErrorCode,
) -> None:
    """Return a distinct stable code for every invalid username-reset bearer class.

    Creates reset state through the email seam and varies only token shape, account binding, or
    frozen age before confirmation while preserving the account and unused token record.

    Arguments:
        client: Django test client issuing reset confirmations.
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
        f"username-classify-{suffix}",
        f"username-classify-{suffix}@localforge.invalid",
        CURRENT_PASSWORD,
        is_active=True,
    )
    other = django_user_model.objects.create_user(
        f"username-foreign-{suffix}",
        f"username-foreign-{suffix}@localforge.invalid",
        CURRENT_PASSWORD,
        is_active=True,
    )
    if kind == "expired":
        with freeze_time("2026-01-01 00:00:00"):
            link = _request_reset_link(client, account, remote_address="192.0.2.207")
        frozen = freeze_time("2026-01-02 00:00:01")
    else:
        link = _request_reset_link(client, account, remote_address="192.0.2.208")
        frozen = freeze_time()

    if kind == "malformed":
        link["token"] = "not-a-username-reset-token"  # noqa: S105
    elif kind == "foreign":
        link["account"] = str(other.pk)

    with frozen:
        response = client.post(
            "/api/v1/users/reset_username_confirm/",
            {**link, "new_username": f"username-classified-{suffix}"},
            content_type="application/json",
            REMOTE_ADDR="192.0.2.209",
        )
    account.refresh_from_db(using="default")

    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert response.json()["code"] == expected_code
    assert account.username == f"username-classify-{suffix}"
    assert (
        UsernameResetToken.objects.using("default")
        .filter(
            account=account,
            used_at__isnull=True,
        )
        .exists()
    )


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@override_settings(
    PASSWORD_RESET_TIMEOUT=3600,
    USERNAME_RESET_TIMEOUT=7200,
)
def test_username_reset_confirm_uses_the_username_lifetime(
    client: DjangoClient,
    django_user_model: type[User],
) -> None:
    """Accept through the username boundary and then report username expiry.

    Issues two bearers while the username lifetime exceeds password recovery, proving exact-boundary
    confirmation succeeds and the next second returns the distinct username expiry code.

    Arguments:
        client: Django test client issuing reset and confirmation requests.
        django_user_model: Configured custom account model.

    Returns:
        None.

    Raises:
        AssertionError: If password lifetime controls either username-reset classification.
    """
    suffix = uuid.uuid4().hex
    valid_account = django_user_model.objects.create_user(
        f"username-timeout-valid-{suffix}",
        f"username-timeout-valid-{suffix}@localforge.invalid",
        CURRENT_PASSWORD,
        is_active=True,
    )
    expired_account = django_user_model.objects.create_user(
        f"username-timeout-expired-{suffix}",
        f"username-timeout-expired-{suffix}@localforge.invalid",
        CURRENT_PASSWORD,
        is_active=True,
    )
    with freeze_time("2026-01-01 00:00:00"):
        valid_link = _request_reset_link(client, valid_account, remote_address="192.0.2.251")
        expired_link = _request_reset_link(client, expired_account, remote_address="192.0.2.252")

    with freeze_time("2026-01-01 02:00:00"):
        valid_response = client.post(
            "/api/v1/users/reset_username_confirm/",
            {**valid_link, "new_username": f"username-timeout-valid-after-{suffix}"},
            content_type="application/json",
            REMOTE_ADDR="192.0.2.253",
        )
    with freeze_time("2026-01-01 02:00:01"):
        expired_response = client.post(
            "/api/v1/users/reset_username_confirm/",
            {**expired_link, "new_username": f"username-timeout-expired-after-{suffix}"},
            content_type="application/json",
            REMOTE_ADDR="192.0.2.254",
        )
    valid_account.refresh_from_db(using="default")
    expired_account.refresh_from_db(using="default")

    assert valid_response.status_code == HTTPStatus.NO_CONTENT
    assert valid_account.username == f"username-timeout-valid-after-{suffix}"
    assert expired_response.status_code == HTTPStatus.BAD_REQUEST
    assert expired_response.json()["code"] == ErrorCode.USERNAME_RESET_TOKEN_EXPIRED
    assert expired_account.username == f"username-timeout-expired-{suffix}"
    assert len(mail.outbox) == EXPECTED_DIVERGENT_TIMEOUT_MESSAGE_COUNT


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_username_reset_task_uses_divergent_username_lifetimes(
    client: DjangoClient,
    django_user_model: type[User],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deliver through a longer username lifetime and suppress shorter stale SMTP.

    Captures two task publications under opposite password and username timeout orderings, proving
    delivery follows only the username protocol's configured age.

    Arguments:
        client: Django test client issuing reset requests.
        django_user_model: Configured custom account model.
        monkeypatch: Fixture capturing publication and rejecting stale SMTP.

    Returns:
        None.

    Raises:
        AssertionError: If password timeout controls delivery or expired work reaches SMTP.
    """
    suffix = uuid.uuid4().hex
    longer_account = django_user_model.objects.create_user(
        f"username-task-longer-{suffix}",
        f"username-task-longer-{suffix}@localforge.invalid",
        CURRENT_PASSWORD,
        is_active=True,
    )
    shorter_account = django_user_model.objects.create_user(
        f"username-task-shorter-{suffix}",
        f"username-task-shorter-{suffix}@localforge.invalid",
        CURRENT_PASSWORD,
        is_active=True,
    )
    captured: list[tuple[str, str]] = []

    def capture_publication(
        *,
        args: tuple[str, str],
        expires: int,
    ) -> None:
        """Capture one divergent-lifetime publication.

        Retains the public task payload for frozen-time execution while proving broker expiry uses
        a positive username-reset lifetime.

        Arguments:
            args: Immutable account key and username-reset bearer.
            expires: Remaining username-reset lifetime.

        Returns:
            None.
        """
        assert expires > 0
        captured.append(args)

    monkeypatch.setattr(send_username_reset_email, "apply_async", capture_publication)
    with (
        override_settings(PASSWORD_RESET_TIMEOUT=3600, USERNAME_RESET_TIMEOUT=7200),
        freeze_time("2026-01-01 00:00:00"),
    ):
        client.post(
            "/api/v1/users/reset_username/",
            {"email": longer_account.email},
            content_type="application/json",
            REMOTE_ADDR="192.0.2.251",
        )
    with (
        override_settings(PASSWORD_RESET_TIMEOUT=3600, USERNAME_RESET_TIMEOUT=7200),
        freeze_time("2026-01-01 01:00:01"),
    ):
        delivered = send_username_reset_email(*captured[0])

    with (
        override_settings(PASSWORD_RESET_TIMEOUT=7200, USERNAME_RESET_TIMEOUT=3600),
        freeze_time("2026-01-01 00:00:00"),
    ):
        client.post(
            "/api/v1/users/reset_username/",
            {"email": shorter_account.email},
            content_type="application/json",
            REMOTE_ADDR="192.0.2.252",
        )

    def reject_stale_smtp(*_args: object, **_kwargs: object) -> bool:
        """Reject any SMTP attempt for the expired shorter-lifetime bearer.

        Raises immediately if task validation reaches delivery after the username protocol lifetime
        while accepting the application email call shape.

        Arguments:
            *_args: Unexpected positional email values.
            **_kwargs: Unexpected named email values.

        Returns:
            Never returns.

        Raises:
            AssertionError: Always.
        """
        message = "expired username-reset task reached SMTP"
        raise AssertionError(message)

    monkeypatch.setattr(account_tasks_module, "send_application_email", reject_stale_smtp)
    with (
        override_settings(PASSWORD_RESET_TIMEOUT=7200, USERNAME_RESET_TIMEOUT=3600),
        freeze_time("2026-01-01 01:00:01"),
    ):
        suppressed = send_username_reset_email(*captured[1])

    assert delivered is True
    assert suppressed is False
    assert len(mail.outbox) == 1


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_username_token_parsing_and_expired_publication_boundaries(
    django_user_model: type[User],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject malformed core shapes and discard publication after expiry.

    Exercises token-helper boundaries not reached by a missing separator and proves an expired
    after-commit callback never reaches the queue adapter.

    Arguments:
        django_user_model: Configured custom account model.
        monkeypatch: Fixture rejecting unexpected queue publication.

    Returns:
        None.

    Raises:
        AssertionError: If malformed or expired token work is accepted.
    """
    with pytest.raises(ValueError, match="shape"):
        username_reset_token_timestamp("invalid.value")
    with pytest.raises(ValueError, match="shape"):
        username_reset_token_timestamp("abc-.")

    account = django_user_model.objects.create_user(
        "username-expired-publication",
        "username-expired-publication@localforge.invalid",
        CURRENT_PASSWORD,
        is_active=True,
    )
    assert username_reset_token_generator.check_token(None, "invalid") is False
    assert username_reset_token_generator.check_token(account, None) is False
    assert username_reset_token_generator.check_token(account, "invalid") is False
    with freeze_time("2026-01-01 00:00:00"):
        token = create_username_reset_token(account)

    def reject_publication(*_args: object, **_kwargs: object) -> None:
        """Reject any queue call after token expiry.

        Fails immediately if the publication adapter is invoked, proving the lifetime helper
        contains stale work first.

        Arguments:
            *_args: Unexpected positional publication values.
            **_kwargs: Unexpected named publication values.

        Returns:
            None.

        Raises:
            AssertionError: Always.
        """
        message = "expired username-reset work reached the queue"
        raise AssertionError(message)

    monkeypatch.setattr(send_username_reset_email, "apply_async", reject_publication)
    with freeze_time("2026-01-02 00:00:01"):
        with pytest.raises(ValueError, match="expired"):
            username_reset_token_remaining_seconds(token)
        dispatch_username_reset_email(str(account.pk), token)


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_unavailable_reset_username_does_not_consume_the_token(
    client: DjangoClient,
    django_user_model: type[User],
) -> None:
    """Return a neutral conflict and permit retry with an available username.

    Uses an occupied case variant first and then a unique replacement with the same bearer, proving
    validation remains per-field while single-use state is consumed only by a successful change.

    Arguments:
        client: Django test client issuing reset confirmations.
        django_user_model: Configured custom account model.

    Returns:
        None.

    Raises:
        AssertionError: If a conflict leaks ownership or consumes the recovery token.
    """
    suffix = uuid.uuid4().hex
    owner = django_user_model.objects.create_user(
        f"OccupiedReset{suffix}",
        f"occupied-reset-{suffix}@localforge.invalid",
        CURRENT_PASSWORD,
        is_active=True,
    )
    account = django_user_model.objects.create_user(
        f"username-reset-candidate-{suffix}",
        f"username-reset-candidate-{suffix}@localforge.invalid",
        CURRENT_PASSWORD,
        is_active=True,
    )
    link = _request_reset_link(client, account, remote_address="192.0.2.210")

    conflict = client.post(
        "/api/v1/users/reset_username_confirm/",
        {**link, "new_username": owner.username.lower()},
        content_type="application/json",
        REMOTE_ADDR="192.0.2.211",
    )
    success = client.post(
        "/api/v1/users/reset_username_confirm/",
        {**link, "new_username": f"username-reset-available-{suffix}"},
        content_type="application/json",
        REMOTE_ADDR="192.0.2.212",
    )
    account.refresh_from_db(using="default")

    assert conflict.status_code == HTTPStatus.BAD_REQUEST
    assert conflict.json()["details"] == {"new_username": ["That username is not available."]}
    assert success.status_code == HTTPStatus.NO_CONTENT
    assert account.username == f"username-reset-available-{suffix}"


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_email_change_deletion_and_used_tombstone_classify_safely(
    client: DjangoClient,
    django_user_model: type[User],
) -> None:
    """Reject stale account state and retain used replay after deletion.

    Changes one bound email, deletes an unused account, and deletes a successfully renamed account,
    proving foreign classification for stale links and used precedence for a consumed tombstone.

    Arguments:
        client: Django test client issuing reset confirmations.
        django_user_model: Configured custom account model.

    Returns:
        None.

    Raises:
        AssertionError: If changed or deleted state leaks or loses replay classification.
    """
    suffix = uuid.uuid4().hex
    changed = django_user_model.objects.create_user(
        f"username-email-{suffix}",
        f"username-email-{suffix}@localforge.invalid",
        CURRENT_PASSWORD,
        is_active=True,
    )
    deleted = django_user_model.objects.create_user(
        f"username-delete-{suffix}",
        f"username-delete-{suffix}@localforge.invalid",
        CURRENT_PASSWORD,
        is_active=True,
    )
    used = django_user_model.objects.create_user(
        f"username-used-{suffix}",
        f"username-used-{suffix}@localforge.invalid",
        CURRENT_PASSWORD,
        is_active=True,
    )
    changed_link = _request_reset_link(client, changed, remote_address="192.0.2.213")
    deleted_link = _request_reset_link(client, deleted, remote_address="192.0.2.214")
    used_link = _request_reset_link(client, used, remote_address="192.0.2.215")
    changed.email = f"username-email-changed-{suffix}@localforge.invalid"
    changed.save(using="default", update_fields=("email", "updated_at"))
    deleted.delete(using="default")
    confirmed = client.post(
        "/api/v1/users/reset_username_confirm/",
        {**used_link, "new_username": f"username-used-after-{suffix}"},
        content_type="application/json",
        REMOTE_ADDR="192.0.2.216",
    )
    used.delete(using="default")

    email_response = client.post(
        "/api/v1/users/reset_username_confirm/",
        {**changed_link, "new_username": f"username-email-after-{suffix}"},
        content_type="application/json",
        REMOTE_ADDR="192.0.2.217",
    )
    deletion_response = client.post(
        "/api/v1/users/reset_username_confirm/",
        {**deleted_link, "new_username": f"username-delete-after-{suffix}"},
        content_type="application/json",
        REMOTE_ADDR="192.0.2.218",
    )
    replay = client.post(
        "/api/v1/users/reset_username_confirm/",
        {**used_link, "new_username": f"username-used-replay-{suffix}"},
        content_type="application/json",
        REMOTE_ADDR="192.0.2.219",
    )

    assert confirmed.status_code == HTTPStatus.NO_CONTENT
    assert email_response.json()["code"] == ErrorCode.USERNAME_RESET_TOKEN_FOREIGN
    assert deletion_response.json()["code"] == ErrorCode.USERNAME_RESET_TOKEN_FOREIGN
    assert replay.json()["code"] == ErrorCode.USERNAME_RESET_TOKEN_USED


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@override_settings(
    USERNAME_RESET_ADDRESS_THROTTLE_RATE=LOW_RESET_RATE,
    USERNAME_RESET_ACCOUNT_THROTTLE_RATE=LOW_RESET_RATE,
    USERNAME_RESET_MINIMUM_RESPONSE_DURATION_SECONDS=0.001,
)
def test_username_reset_throttles_are_exact_recoverable_and_ignore_invalid_bodies(
    client: DjangoClient,
    django_user_model: type[User],
) -> None:
    """Charge only valid request and confirmation bodies and recover after expiry.

    Exercises route-specific address plus stable recipient or account dimensions, ages their
    primary events beyond the rolling window, and proves valid traffic resumes without cache state.

    Arguments:
        client: Django test client issuing reset requests.
        django_user_model: Configured custom account model.

    Returns:
        None.

    Raises:
        AssertionError: If invalid input consumes quota or expired events continue blocking.
    """
    suffix = uuid.uuid4().hex
    account = django_user_model.objects.create_user(
        f"username-throttle-{suffix}",
        f"username-throttle-{suffix}@localforge.invalid",
        CURRENT_PASSWORD,
        is_active=True,
    )
    address = "192.0.2.220"
    invalid_request = client.post(
        "/api/v1/users/reset_username/",
        {"email": "not-an-address"},
        content_type="application/json",
        REMOTE_ADDR=address,
    )
    first_request = client.post(
        "/api/v1/users/reset_username/",
        {"email": account.email},
        content_type="application/json",
        REMOTE_ADDR=address,
    )
    denied_request = client.post(
        "/api/v1/users/reset_username/",
        {"email": account.email},
        content_type="application/json",
        REMOTE_ADDR=address,
    )
    LoginThrottleEvent.objects.using("default").filter(bucket__startswith="username-reset-").update(
        occurred_at=timezone.now() - timedelta(hours=2)
    )
    recovered_request = client.post(
        "/api/v1/users/reset_username/",
        {"email": account.email},
        content_type="application/json",
        REMOTE_ADDR=address,
    )
    message = cast("EmailMultiAlternatives", mail.outbox[-1])
    query = parse_qs(urlparse(message.body.split("Continue at ", maxsplit=1)[1].strip()).query)
    link = {"account": query["account"][0], "token": query["token"][0]}
    confirm_address = "192.0.2.221"
    invalid_confirm = client.post(
        "/api/v1/users/reset_username_confirm/",
        {**link, "new_username": "not allowed"},
        content_type="application/json",
        REMOTE_ADDR=confirm_address,
    )
    conflict_name = account.username
    conflict = client.post(
        "/api/v1/users/reset_username_confirm/",
        {**link, "new_username": conflict_name},
        content_type="application/json",
        REMOTE_ADDR=confirm_address,
    )
    denied_confirm = client.post(
        "/api/v1/users/reset_username_confirm/",
        {**link, "new_username": f"username-throttle-after-{suffix}"},
        content_type="application/json",
        REMOTE_ADDR=confirm_address,
    )
    LoginThrottleEvent.objects.using("default").filter(
        bucket__startswith="username-reset-confirm-"
    ).update(occurred_at=timezone.now() - timedelta(hours=2))
    recovered_confirm = client.post(
        "/api/v1/users/reset_username_confirm/",
        {**link, "new_username": f"username-throttle-after-{suffix}"},
        content_type="application/json",
        REMOTE_ADDR=confirm_address,
    )

    assert invalid_request.status_code == HTTPStatus.BAD_REQUEST
    assert first_request.status_code == HTTPStatus.ACCEPTED
    assert denied_request.status_code == HTTPStatus.TOO_MANY_REQUESTS
    assert denied_request.headers["Retry-After"]
    assert recovered_request.status_code == HTTPStatus.ACCEPTED
    assert invalid_confirm.status_code == HTTPStatus.BAD_REQUEST
    assert conflict.status_code == HTTPStatus.NO_CONTENT
    assert denied_confirm.status_code == HTTPStatus.TOO_MANY_REQUESTS
    assert recovered_confirm.json()["code"] == ErrorCode.USERNAME_RESET_TOKEN_USED


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_username_reset_admission_fails_closed_when_postgres_is_unavailable(
    client: DjangoClient,
    django_user_model: type[User],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Return correlated service-unavailable envelopes for both admission boundaries.

    Creates a valid link before replacing the authoritative admission store with a database
    failure, proving neither public route falls back to process-local or cache-backed quota.

    Arguments:
        client: Django test client issuing reset requests.
        django_user_model: Configured custom account model.
        monkeypatch: Fixture replacing the authoritative store boundary.

    Returns:
        None.

    Raises:
        AssertionError: If either route admits while PostgreSQL cannot decide.
    """
    suffix = uuid.uuid4().hex
    account = django_user_model.objects.create_user(
        f"username-outage-{suffix}",
        f"username-outage-{suffix}@localforge.invalid",
        CURRENT_PASSWORD,
        is_active=True,
    )
    link = _request_reset_link(client, account, remote_address="192.0.2.222")

    def fail_admission(
        _store: PostgresLoginThrottleStore,
        _rules: object,
        *,
        member: str,
    ) -> object:
        """Raise one authoritative admission failure.

        Accepts the public store call shape while exposing no submitted field in the exception.
        This models loss of the primary security state rather than an ordinary denied decision.

        Arguments:
            _store: Store instance receiving the call.
            _rules: Opaque rolling-window rules.
            member: Correlated request member.

        Returns:
            Never returns.

        Raises:
            DatabaseError: Always.
        """
        del member
        raise DatabaseError

    monkeypatch.setattr(PostgresLoginThrottleStore, "admit", fail_admission)
    request = client.post(
        "/api/v1/users/reset_username/",
        {"email": account.email},
        content_type="application/json",
        REMOTE_ADDR="192.0.2.223",
    )
    confirm = client.post(
        "/api/v1/users/reset_username_confirm/",
        {**link, "new_username": f"username-outage-after-{suffix}"},
        content_type="application/json",
        REMOTE_ADDR="192.0.2.224",
    )

    assert request.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert confirm.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert request.json()["code"] == ErrorCode.SERVICE_UNAVAILABLE
    assert confirm.json()["code"] == ErrorCode.SERVICE_UNAVAILABLE
    assert request.json()["request_id"] == request.headers["X-Request-ID"]
    assert confirm.json()["request_id"] == confirm.headers["X-Request-ID"]


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_username_reset_common_lookup_loss_returns_service_unavailable(
    client: DjangoClient,
    django_user_model: type[User],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail closed when the view's post-admission account lookup is lost.

    Allows the throttle's first locked resolution and fails the view's second resolution, proving
    common outcome establishment never falls back to dummy success after admission.

    Arguments:
        client: Django test client issuing the reset request.
        django_user_model: Configured custom account model.
        monkeypatch: Fixture replacing the account resolution boundary.

    Returns:
        None.

    Raises:
        AssertionError: If common lookup loss returns accepted.
    """
    suffix = uuid.uuid4().hex
    account = django_user_model.objects.create_user(
        f"username-lookup-{suffix}",
        f"username-lookup-{suffix}@localforge.invalid",
        CURRENT_PASSWORD,
        is_active=True,
    )
    original = username_management_module.resolve_username_reset_account
    calls = 0

    def fail_second_lookup(email: str) -> User | None:
        """Resolve admission and fail the subsequent view lookup.

        Counts the two public-request resolutions while preserving the real first result.
        The failure therefore occurs after a valid authoritative admission decision.

        Arguments:
            email: Normalized submitted address.

        Returns:
            Real account for the first call.

        Raises:
            DatabaseError: On the second call.
        """
        nonlocal calls
        calls += 1
        if calls == SECOND_ACCOUNT_LOOKUP:
            raise DatabaseError
        return original(email)

    monkeypatch.setattr(
        username_management_module,
        "resolve_username_reset_account",
        fail_second_lookup,
    )
    response = client.post(
        "/api/v1/users/reset_username/",
        {"email": account.email},
        content_type="application/json",
        REMOTE_ADDR="192.0.2.242",
    )

    assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert response.json()["code"] == ErrorCode.SERVICE_UNAVAILABLE


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@pytest.mark.parametrize(
    ("transition", "expected_code"),
    [
        pytest.param("record-deleted", ErrorCode.USERNAME_RESET_TOKEN_FOREIGN),
        pytest.param("used", ErrorCode.USERNAME_RESET_TOKEN_USED),
        pytest.param("expired", ErrorCode.USERNAME_RESET_TOKEN_EXPIRED),
    ],
)
def test_username_reset_confirm_rechecks_state_after_preflight(
    client: DjangoClient,
    django_user_model: type[User],
    monkeypatch: pytest.MonkeyPatch,
    transition: str,
    expected_code: ErrorCode,
) -> None:
    """Classify record loss, use, and expiry that win after preflight.

    Applies one competing authoritative transition immediately before the locked implementation,
    proving confirmation never trusts the earlier read and preserves used-before-expired precedence.

    Arguments:
        client: Django test client issuing reset confirmation.
        django_user_model: Configured custom account model.
        monkeypatch: Fixture applying the competing transition.
        transition: State change to commit after preflight.
        expected_code: Stable classification expected at the public boundary.

    Returns:
        None.

    Raises:
        AssertionError: If locked revalidation accepts stale preflight state.
    """
    suffix = uuid.uuid4().hex
    account = django_user_model.objects.create_user(
        f"username-preflight-{suffix}",
        f"username-preflight-{suffix}@localforge.invalid",
        CURRENT_PASSWORD,
        is_active=True,
    )
    link = _request_reset_link(client, account, remote_address="192.0.2.243")
    original = cast(
        "Callable[[uuid.UUID, int, str, str, str], None]",
        vars(username_management_module)["_apply_locked_username_reset"],
    )

    def transition_then_apply(
        account_id: uuid.UUID,
        record_id: int,
        digest: str,
        token: str,
        new_username: str,
    ) -> None:
        """Apply one competing transition before locked confirmation.

        Mutates only authoritative token state or time and then delegates to the real locked
        implementation.

        Arguments:
            account_id: Immutable submitted account key.
            record_id: Preflighted token record key.
            digest: Submitted bearer digest.
            token: Submitted bearer.
            new_username: Valid replacement username.

        Returns:
            None.

        Raises:
            APIException: From the real locked classification.
        """
        if transition == "record-deleted":
            UsernameResetToken.objects.using("default").filter(pk=record_id).delete()
            original(account_id, record_id, digest, token, new_username)
            return
        if transition == "used":
            UsernameResetToken.objects.using("default").filter(pk=record_id).update(
                used_at=timezone.now()
            )
            original(account_id, record_id, digest, token, new_username)
            return
        with freeze_time(timezone.now() + timedelta(days=2)):
            original(account_id, record_id, digest, token, new_username)

    monkeypatch.setattr(
        username_management_module,
        "_apply_locked_username_reset",
        transition_then_apply,
    )
    response = client.post(
        "/api/v1/users/reset_username_confirm/",
        {**link, "new_username": f"username-preflight-after-{suffix}"},
        content_type="application/json",
        REMOTE_ADDR="192.0.2.244",
    )

    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert response.json()["code"] == expected_code


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_username_change_contains_account_disappearance_and_database_failure(
    client: DjangoClient,
    django_user_model: type[User],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Normalize locked account disappearance and write failure.

    Calls the transition with a missing authenticated identity, then drives a real HTTP request
    whose authoritative save fails, proving stable authentication and dependency envelopes.

    Arguments:
        client: Django test client issuing the username change.
        django_user_model: Configured custom account model.
        monkeypatch: Fixture replacing the persistence boundary.

    Returns:
        None.

    Raises:
        AssertionError: If either internal state failure escapes its public classification.
    """
    with pytest.raises(AuthenticationFailed):
        change_username(
            uuid.uuid4(),
            {"current_password": CURRENT_PASSWORD, "new_username": "missing-account"},
        )

    account = django_user_model.objects.create_user(
        "username-database",
        "username-database@localforge.invalid",
        CURRENT_PASSWORD,
        is_active=True,
    )
    reset_account = django_user_model.objects.create_user(
        "username-reset-database",
        "username-reset-database@localforge.invalid",
        CURRENT_PASSWORD,
        is_active=True,
    )
    reset_link = _request_reset_link(client, reset_account, remote_address="192.0.2.245")
    token = Token.objects.using("default").create(user=account)

    def fail_save(_account: User, _new_username: str) -> None:
        """Raise one authoritative username persistence failure.

        Models database loss after password verification without exposing account fields.
        The same boundary is shared by authenticated and recovery changes.

        Arguments:
            _account: Locked account being renamed.
            _new_username: Validated replacement identifier.

        Returns:
            Never returns.

        Raises:
            DatabaseError: Always.
        """
        raise DatabaseError

    monkeypatch.setattr(username_management_module, "_save_username", fail_save)
    response = client.post(
        "/api/v1/users/set_username/",
        {"current_password": CURRENT_PASSWORD, "new_username": "username-database-after"},
        content_type="application/json",
        headers={"authorization": f"Token {token.key}"},
    )
    reset_response = client.post(
        "/api/v1/users/reset_username_confirm/",
        {**reset_link, "new_username": "username-reset-database-after"},
        content_type="application/json",
        REMOTE_ADDR="192.0.2.246",
    )

    assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert response.json()["code"] == ErrorCode.SERVICE_UNAVAILABLE
    assert reset_response.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert reset_response.json()["code"] == ErrorCode.SERVICE_UNAVAILABLE


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@override_settings(USERNAME_RESET_MINIMUM_RESPONSE_DURATION_SECONDS=0.001)
def test_username_reset_token_store_failure_is_indistinguishable_and_redacted(
    client: DjangoClient,
    django_user_model: type[User],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Fall back to accepted dummy work after partial token persistence failure.

    Raises after inserting the real digest so the savepoint must roll it back, then verifies the
    public response, task outcome, database state, and structured logs reveal no account fields.

    Arguments:
        client: Django test client issuing the reset request.
        django_user_model: Configured custom account model.
        monkeypatch: Fixture replacing token persistence.
        caplog: Captured application log records.

    Returns:
        None.

    Raises:
        AssertionError: If partial state survives or public and logged output disclose identity.
    """
    suffix = uuid.uuid4().hex
    account = django_user_model.objects.create_user(
        f"username-store-{suffix}",
        f"username-store-{suffix}@localforge.invalid",
        CURRENT_PASSWORD,
        is_active=True,
    )
    original_create = QuerySet.create

    def fail_token_insert(queryset: QuerySet[Any], **kwargs: object) -> object:
        """Insert then fail only the username-reset record.

        Preserves normal admission writes while forcing the request's token savepoint to prove
        rollback and dummy-publication behavior.

        Arguments:
            queryset: Model queryset receiving the create.
            **kwargs: Field values supplied to the create.

        Returns:
            Newly created object for unrelated models.

        Raises:
            DatabaseError: After inserting a username-reset record.
        """
        created = original_create(queryset, **kwargs)
        if queryset.model is UsernameResetToken:
            raise DatabaseError
        return created

    monkeypatch.setattr(QuerySet, "create", fail_token_insert)
    caplog.set_level(logging.ERROR)
    response = client.post(
        "/api/v1/users/reset_username/",
        {"email": account.email},
        content_type="application/json",
        REMOTE_ADDR="192.0.2.225",
    )
    log_text = "\n".join(
        StructuredFormatter().format(record)
        for record in caplog.records
        if record.name == username_management_module.__name__
    )

    assert response.status_code == HTTPStatus.ACCEPTED
    assert response.json() == {"detail": RESET_ACCEPTED_MESSAGE}
    assert not UsernameResetToken.objects.using("default").filter(account=account).exists()
    assert not mail.outbox
    assert "Username reset token persistence failed" in log_text
    assert account.username not in log_text
    assert account.email not in log_text
    assert str(account.pk) not in log_text


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_username_reset_task_is_single_attempt_and_revalidates_state(
    client: DjangoClient,
    django_user_model: type[User],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deliver at most once and reject changed, deleted, used, and expired task state.

    Captures publication arguments before eager execution, invokes the public task seam repeatedly,
    and mutates authoritative account state between independent issuances.

    Arguments:
        client: Django test client issuing reset requests.
        django_user_model: Configured custom account model.
        monkeypatch: Fixture capturing task publication.

    Returns:
        None.

    Raises:
        AssertionError: If redelivery or stale state can send a username-reset email.
    """
    suffix = uuid.uuid4().hex
    account = django_user_model.objects.create_user(
        f"username-task-{suffix}",
        f"username-task-{suffix}@localforge.invalid",
        CURRENT_PASSWORD,
        is_active=True,
    )
    captured: list[tuple[str, str]] = []

    def capture_publication(
        *,
        args: tuple[str, str],
        expires: int,
    ) -> None:
        """Capture one expiring username-reset task publication.

        Retains only the immutable account key and bearer for direct task invocation while proving
        the broker expiry is positive.

        Arguments:
            args: Account key and reset bearer.
            expires: Remaining token lifetime.

        Returns:
            None.
        """
        assert expires > 0
        captured.append(args)

    monkeypatch.setattr(send_username_reset_email, "apply_async", capture_publication)
    client.post(
        "/api/v1/users/reset_username/",
        {"email": account.email},
        content_type="application/json",
        REMOTE_ADDR="192.0.2.226",
    )
    first = send_username_reset_email(*captured[-1])
    duplicate = send_username_reset_email(*captured[-1])

    client.post(
        "/api/v1/users/reset_username/",
        {"email": account.email},
        content_type="application/json",
        REMOTE_ADDR="192.0.2.227",
    )
    account.email = f"username-task-changed-{suffix}@localforge.invalid"
    account.save(using="default", update_fields=("email", "updated_at"))
    changed = send_username_reset_email(*captured[-1])

    with freeze_time("2026-01-01 00:00:00"):
        client.post(
            "/api/v1/users/reset_username/",
            {"email": account.email},
            content_type="application/json",
            REMOTE_ADDR="192.0.2.228",
        )
        expired_args = captured[-1]
    with freeze_time("2026-01-02 00:00:01"):
        expired = send_username_reset_email(*expired_args)

    assert first is True
    assert duplicate is False
    assert changed is False
    assert expired is False
    assert len(mail.outbox) == 1
    assert send_username_reset_email("not-a-uuid", "not-a-token") is False
    assert deliver_username_reset_email(object(), object()) is False


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@pytest.mark.parametrize("transition", ["account-deleted", "record-used", "email-rejected"])
def test_username_reset_task_revalidates_after_durable_claim(
    client: DjangoClient,
    django_user_model: type[User],
    monkeypatch: pytest.MonkeyPatch,
    transition: str,
) -> None:
    """Reject state loss or SMTP failure after the durable delivery claim.

    Wraps the real claim to apply a competing account or token transition before second validation,
    or rejects the final email boundary, proving no stale delivery is marked complete.

    Arguments:
        client: Django test client issuing the reset request.
        django_user_model: Configured custom account model.
        monkeypatch: Fixture controlling publication and post-claim state.
        transition: Competing state or delivery failure to apply.

    Returns:
        None.

    Raises:
        AssertionError: If stale or rejected delivery succeeds.
    """
    suffix = uuid.uuid4().hex
    account = django_user_model.objects.create_user(
        f"username-claim-{suffix}",
        f"username-claim-{suffix}@localforge.invalid",
        CURRENT_PASSWORD,
        is_active=True,
    )
    captured: list[tuple[str, str]] = []

    def capture_publication(
        *,
        args: tuple[str, str],
        expires: int,
    ) -> None:
        """Capture one task for controlled direct execution.

        Preserves broker-expiry validation while delaying task work until the competing transition
        is installed.

        Arguments:
            args: Immutable account key and bearer.
            expires: Remaining token lifetime.

        Returns:
            None.
        """
        assert expires > 0
        captured.append(args)

    monkeypatch.setattr(send_username_reset_email, "apply_async", capture_publication)
    client.post(
        "/api/v1/users/reset_username/",
        {"email": account.email},
        content_type="application/json",
        REMOTE_ADDR="192.0.2.247",
    )
    original_claim = cast(
        "Callable[[uuid.UUID, str, str], account_tasks_module.UsernameResetDeliveryClaim | None]",
        vars(account_tasks_module)["_claim_username_reset_delivery"],
    )

    if transition == "email-rejected":

        def reject_email(*_args: object, **_kwargs: object) -> bool:
            """Reject the final application email boundary.

            Models an SMTP backend refusal after the durable claim while accepting every call
            shape the delivery task supplies.

            Arguments:
                *_args: Positional email values.
                **_kwargs: Named email values.

            Returns:
                Always false.
            """
            return False

        monkeypatch.setattr(account_tasks_module, "send_application_email", reject_email)
    else:

        def claim_then_mutate(
            subject_id: uuid.UUID,
            digest: str,
            token: str,
        ) -> object:
            """Apply one competing transition after the real durable claim.

            Delegates the complete account-before-token claim, commits it, then removes live
            account or unused-record eligibility before second validation.

            Arguments:
                subject_id: Immutable account key.
                digest: Submitted bearer digest.
                token: Submitted bearer.

            Returns:
                Real durable claim.
            """
            claim = original_claim(subject_id, digest, token)
            assert claim is not None
            if transition == "account-deleted":
                django_user_model.objects.using("default").filter(pk=subject_id).delete()
            else:
                UsernameResetToken.objects.using("default").filter(pk=claim.record_id).update(
                    used_at=timezone.now()
                )
            return claim

        monkeypatch.setattr(
            account_tasks_module,
            "_claim_username_reset_delivery",
            claim_then_mutate,
        )

    delivered = send_username_reset_email(*captured[0])
    record = UsernameResetToken.objects.using("default").filter(subject_id=account.pk).first()

    assert delivered is False
    assert not mail.outbox
    if record is not None:
        assert record.delivered_at is None


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_username_reset_publication_failure_is_contained_and_redacted(
    client: DjangoClient,
    django_user_model: type[User],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Contain broker publication failure without exposing username, email, or bearer.

    Replaces only the external task-publication boundary and verifies the accepted response,
    digest-only state, and structured error record remain safe.

    Arguments:
        client: Django test client issuing the reset request.
        django_user_model: Configured custom account model.
        monkeypatch: Fixture replacing queue publication.
        caplog: Captured application log records.

    Returns:
        None.

    Raises:
        AssertionError: If the failure escapes or sensitive account data enters logs.
    """
    suffix = uuid.uuid4().hex
    account = django_user_model.objects.create_user(
        f"username-publish-{suffix}",
        f"username-publish-{suffix}@localforge.invalid",
        CURRENT_PASSWORD,
        is_active=True,
    )
    captured_token = ""

    def fail_publication(
        *,
        args: tuple[str, str],
        expires: int,
    ) -> None:
        """Raise one queue publication failure.

        Captures the bearer solely for the test's redaction assertion before simulating an external
        transport error.

        Arguments:
            args: Account key and reset bearer.
            expires: Remaining task lifetime.

        Returns:
            Never returns.

        Raises:
            OSError: Always.
        """
        nonlocal captured_token
        assert expires > 0
        captured_token = args[1]
        raise OSError

    monkeypatch.setattr(send_username_reset_email, "apply_async", fail_publication)
    caplog.set_level(logging.ERROR)
    response = client.post(
        "/api/v1/users/reset_username/",
        {"email": account.email},
        content_type="application/json",
        REMOTE_ADDR="192.0.2.229",
    )
    log_text = "\n".join(
        StructuredFormatter().format(record)
        for record in caplog.records
        if record.name == username_management_module.__name__
    )

    assert response.status_code == HTTPStatus.ACCEPTED
    assert UsernameResetToken.objects.using("default").filter(account=account).exists()
    assert "Username reset email publication failed" in log_text
    assert account.username not in log_text
    assert account.email not in log_text
    assert str(account.pk) not in log_text
    assert captured_token not in log_text


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@pytest.mark.timeout(90)
def test_concurrent_username_reset_confirm_has_one_winner_and_one_used_replay(
    client: DjangoClient,
    django_user_model: type[User],
) -> None:
    """Serialize concurrent confirmation so exactly one rename wins.

    Starts two independent requests with the same bearer and distinct valid replacements, proving
    account-before-token locks make the loser observe the consumed record as used.

    Arguments:
        client: Django test client preparing reset state.
        django_user_model: Configured custom account model.

    Returns:
        None.

    Raises:
        AssertionError: If both requests succeed or replay classification is unstable.
    """
    suffix = uuid.uuid4().hex
    account = django_user_model.objects.create_user(
        f"username-race-{suffix}",
        f"username-race-{suffix}@localforge.invalid",
        CURRENT_PASSWORD,
        is_active=True,
    )
    link = _request_reset_link(client, account, remote_address="192.0.2.230")
    barrier = Barrier(2)

    def confirm(index: int) -> tuple[int, object]:
        """Post one racing confirmation from an independent connection.

        Synchronizes request start and closes thread-local database state after the public HTTP
        response completes.

        Arguments:
            index: Replacement suffix and client address discriminator.

        Returns:
            Response status and parsed body when present.
        """
        close_old_connections()
        try:
            barrier.wait(timeout=RACE_WAIT_SECONDS)
            response = DjangoClient().post(
                "/api/v1/users/reset_username_confirm/",
                {**link, "new_username": f"username-race-after-{suffix}-{index}"},
                content_type="application/json",
                REMOTE_ADDR=f"192.0.2.{230 + index}",
            )
            body = response.json() if response.content else None
            return response.status_code, body
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(confirm, (1, 2)))

    statuses = sorted(status for status, _body in results)
    errors = [
        cast("dict[str, object]", body)["code"]
        for status, body in results
        if status == HTTPStatus.BAD_REQUEST
    ]
    account.refresh_from_db(using="default")

    assert statuses == [HTTPStatus.NO_CONTENT, HTTPStatus.BAD_REQUEST]
    assert errors == [ErrorCode.USERNAME_RESET_TOKEN_USED]
    assert account.username in {
        f"username-race-after-{suffix}-1",
        f"username-race-after-{suffix}-2",
    }


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@pytest.mark.timeout(90)
def test_concurrent_case_insensitive_username_conflict_has_one_winner(
    django_user_model: type[User],
) -> None:
    """Let PostgreSQL choose one winner for concurrent lowercase-equivalent names.

    Starts authenticated changes on independent accounts and connections, proving the functional
    constraint closes the pre-check race while the loser receives the neutral field response.

    Arguments:
        django_user_model: Configured custom account model.

    Returns:
        None.

    Raises:
        AssertionError: If both names persist or a database error escapes the API.
    """
    suffix = uuid.uuid4().hex
    accounts = [
        django_user_model.objects.create_user(
            f"username-conflict-{suffix}-{index}",
            f"username-conflict-{suffix}-{index}@localforge.invalid",
            CURRENT_PASSWORD,
            is_active=True,
        )
        for index in (1, 2)
    ]
    tokens = [Token.objects.using("default").create(user=account) for account in accounts]
    barrier = Barrier(2)

    def rename(index: int) -> tuple[int, object]:
        """Post one racing authenticated username change.

        Uses opposite letter case for the shared PostgreSQL identity and closes thread-local
        database state after the response.

        Arguments:
            index: Account and replacement-case selector.

        Returns:
            Response status and parsed body when present.
        """
        close_old_connections()
        try:
            barrier.wait(timeout=RACE_WAIT_SECONDS)
            response = DjangoClient().post(
                "/api/v1/users/set_username/",
                {
                    "current_password": CURRENT_PASSWORD,
                    "new_username": (
                        f"SharedUsername{suffix}" if index == 0 else f"sharedusername{suffix}"
                    ),
                },
                content_type="application/json",
                headers={"authorization": f"Token {tokens[index].key}"},
            )
            body = response.json() if response.content else None
            return response.status_code, body
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(rename, (0, 1)))

    statuses = sorted(status for status, _body in results)
    errors = [
        cast("dict[str, Any]", body)["details"]
        for status, body in results
        if status == HTTPStatus.BAD_REQUEST
    ]

    assert statuses == [HTTPStatus.NO_CONTENT, HTTPStatus.BAD_REQUEST]
    assert errors == [{"new_username": ["That username is not available."]}]


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_username_constraint_race_returns_neutral_validation(
    client: DjangoClient,
    django_user_model: type[User],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Translate a database uniqueness race into the public field response.

    Forces the authoritative save to lose after its availability pre-check, proving the
    PostgreSQL constraint fallback never escapes as a server error or owner disclosure.

    Arguments:
        client: Django test client issuing the username change.
        django_user_model: Configured custom account model.
        monkeypatch: Fixture simulating the concurrent constraint winner.

    Returns:
        None.

    Raises:
        AssertionError: If the database race bypasses neutral validation.
    """
    account = django_user_model.objects.create_user(
        "username-forced-race",
        "username-forced-race@localforge.invalid",
        CURRENT_PASSWORD,
        is_active=True,
    )
    token = Token.objects.using("default").create(user=account)
    original_save = django_user_model.save

    def fail_selected_save(instance: User, *args: object, **kwargs: object) -> None:
        """Raise a uniqueness conflict only for the selected rename.

        Delegates every unrelated save while forcing the precise post-check persistence race under
        the public request seam.

        Arguments:
            instance: Account instance being persisted.
            *args: Positional save arguments.
            **kwargs: Named save arguments.

        Returns:
            None.

        Raises:
            IntegrityError: For the selected replacement username.
        """
        if instance.pk == account.pk and instance.username == "username-forced-race-after":
            raise IntegrityError
        original_save(instance, *args, **kwargs)

    monkeypatch.setattr(django_user_model, "save", fail_selected_save)
    response = client.post(
        "/api/v1/users/set_username/",
        {
            "current_password": CURRENT_PASSWORD,
            "new_username": "username-forced-race-after",
        },
        content_type="application/json",
        headers={"authorization": f"Token {token.key}"},
    )

    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert response.json()["details"] == {"new_username": ["That username is not available."]}


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_username_routes_document_and_reach_complete_framework_contract(
    client: DjangoClient,
    django_user_model: type[User],
) -> None:
    """Document every reachable status and exercise framework-owned failures.

    Generates public OpenAPI, verifies no extra methods, checks examples on every error response,
    and reaches method, negotiation, media, and authentication errors through HTTP.

    Arguments:
        client: Django test client issuing framework-level requests.
        django_user_model: Configured custom account model.

    Returns:
        None.

    Raises:
        AssertionError: If schema or runtime framework behavior omits the shared contract.
    """
    schema = cast(
        "dict[str, Any]",
        SchemaGenerator().get_schema(request=None, public=True),  # type: ignore[no-untyped-call]
    )
    paths = cast("dict[str, Any]", schema["paths"])
    expected = {
        "/api/v1/users/set_username/": {
            "204",
            "400",
            "401",
            "405",
            "406",
            "413",
            "415",
            "500",
            "503",
        },
        "/api/v1/users/reset_username/": {
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
        "/api/v1/users/reset_username_confirm/": {
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

    set_description = cast(
        "str",
        paths["/api/v1/users/set_username/"]["post"]["description"],
    ).lower()
    confirm_description = cast(
        "str",
        paths["/api/v1/users/reset_username_confirm/"]["post"]["description"],
    ).lower()
    assert "does not invalidate" in set_description
    assert "without revoking" in confirm_description

    account = django_user_model.objects.create_user(
        "username-framework",
        "username-framework@localforge.invalid",
        CURRENT_PASSWORD,
        is_active=True,
    )
    token = Token.objects.using("default").create(user=account)
    method = client.get(
        "/api/v1/users/set_username/",
        headers={"authorization": f"Token {token.key}"},
    )
    reset_method = client.get("/api/v1/users/reset_username/")
    confirm_method = client.get("/api/v1/users/reset_username_confirm/")
    negotiation = client.post(
        "/api/v1/users/reset_username/",
        {"email": account.email},
        content_type="application/json",
        headers={"accept": "application/xml"},
    )
    media = client.post(
        "/api/v1/users/reset_username_confirm/",
        "account=value",
        content_type="text/plain",
    )

    assert method.json()["code"] == ErrorCode.METHOD_NOT_ALLOWED
    assert reset_method.json()["code"] == ErrorCode.METHOD_NOT_ALLOWED
    assert confirm_method.json()["code"] == ErrorCode.METHOD_NOT_ALLOWED
    assert negotiation.json()["code"] == ErrorCode.NOT_ACCEPTABLE
    assert media.json()["code"] == ErrorCode.UNSUPPORTED_MEDIA_TYPE


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.security_timing
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@override_settings(
    USERNAME_RESET_ADDRESS_THROTTLE_RATE=HIGH_RESET_RATE,
    USERNAME_RESET_ACCOUNT_THROTTLE_RATE=HIGH_RESET_RATE,
)
def test_username_reset_request_outcomes_meet_the_approved_timing_criterion(
    client: DjangoClient,
    django_user_model: type[User],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep every accepted username-reset median within the approved 5-plus-30 bound.

    Suppresses task execution, measures active, inactive, unknown, and token-store-failure requests,
    and enforces the greater of twenty percent or ten milliseconds across all medians.

    Arguments:
        client: Django test client issuing measured reset requests.
        django_user_model: Configured custom account model.
        monkeypatch: Fixture replacing publication and selected token persistence.

    Returns:
        None.

    Raises:
        AssertionError: If bodies differ or median timing exceeds the approved bound.
    """
    suffix = uuid.uuid4().hex
    active = django_user_model.objects.create_user(
        f"username-timing-{suffix}",
        f"username-timing-{suffix}@localforge.invalid",
        CURRENT_PASSWORD,
        is_active=True,
    )
    inactive = django_user_model.objects.create_user(
        f"username-timing-inactive-{suffix}",
        f"username-timing-inactive-{suffix}@localforge.invalid",
        CURRENT_PASSWORD,
    )
    failure_email = f"username-timing-failure-{suffix}@localforge.invalid"
    django_user_model.objects.create_user(
        f"username-timing-failure-{suffix}",
        failure_email,
        CURRENT_PASSWORD,
        is_active=True,
    )

    def discard_publication(
        *,
        args: tuple[str, str],
        expires: int,
    ) -> None:
        """Accept one username-reset publication without running email work.

        Keeps timing measurements on request admission, token issuance, and the configured floor
        rather than eager SMTP behavior.

        Arguments:
            args: Account and bearer task arguments.
            expires: Broker expiry calculated by the request.

        Returns:
            None.
        """
        assert len(args) == EXPECTED_TASK_ARGUMENT_COUNT
        assert expires > 0

    monkeypatch.setattr(send_username_reset_email, "apply_async", discard_publication)
    original_create = QuerySet.create

    def fail_selected_token_insert(queryset: QuerySet[Any], **kwargs: object) -> object:
        """Fail username-token insertion only for the selected active account.

        Preserves every normal account and admission write while forcing one accepted fallback
        timing population.

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
            queryset.model is UsernameResetToken
            and isinstance(candidate, django_user_model)
            and candidate.email == failure_email
        ):
            raise DatabaseError
        return created

    monkeypatch.setattr(QuerySet, "create", fail_selected_token_insert)

    def measure(email: str, prefix: str) -> tuple[list[float], object]:
        """Measure one username-reset outcome through the public route.

        Performs the approved warmup and sample counts while assigning independent client
        dimensions to avoid rate-limit interference.

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
                "/api/v1/users/reset_username/",
                {"email": email},
                content_type="application/json",
                REMOTE_ADDR=f"198.52.{prefix}.{index + 1}",
            )
            elapsed = time.perf_counter() - started
            assert response.status_code == HTTPStatus.ACCEPTED
            payload = response.json()
            if index >= TIMING_WARMUP_REQUESTS:
                durations.append(elapsed)
        return durations, payload

    measured = {
        "active": measure(active.email, "100"),
        "inactive": measure(inactive.email, "101"),
        "unknown": measure(f"username-timing-unknown-{suffix}@localforge.invalid", "102"),
        "token_store_failure": measure(failure_email, "103"),
    }
    medians = {name: statistics.median(samples) for name, (samples, _body) in measured.items()}
    delta = max(medians.values()) - min(medians.values())
    allowed = max(max(medians.values()) * TIMING_RELATIVE_LIMIT, TIMING_ABSOLUTE_LIMIT_SECONDS)
    timing_logger.info(
        (
            "username reset timing active=%.6fs inactive=%.6fs unknown=%.6fs "
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
@pytest.mark.services("postgres", "mailpit")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@pytest.mark.skipif(
    settings.EMAIL_BACKEND != SMTP_BACKEND,
    reason="requires the testing SMTP profile",
)
@override_settings(EMAIL_BACKEND=SMTP_BACKEND)
def test_username_recovery_round_trips_through_smtp_and_mailpit(
    client: DjangoClient,
    django_user_model: type[User],
) -> None:
    """Complete username recovery through real SMTP capture.

    Clears persistent Mailpit state, requests and reads the reset email through its API, confirms
    the extracted link, and observes the credential-free username-change notification.

    Arguments:
        client: Django test client issuing the recovery loop.
        django_user_model: Configured custom account model.

    Returns:
        None.

    Raises:
        AssertionError: If either multipart message or the public confirmation fails.
    """
    _mailpit_request("DELETE", "/api/v1/messages")
    suffix = uuid.uuid4().hex
    account = django_user_model.objects.create_user(
        f"username-smtp-before-{suffix}",
        f"username-smtp-{suffix}@localforge.invalid",
        CURRENT_PASSWORD,
        is_active=True,
    )
    requested = client.post(
        "/api/v1/users/reset_username/",
        {"email": account.email},
        content_type="application/json",
        REMOTE_ADDR="192.0.2.240",
    )
    assert requested.status_code == HTTPStatus.ACCEPTED

    deadline = time.monotonic() + MAILPIT_TIMEOUT_SECONDS
    link: dict[str, str] | None = None
    while time.monotonic() < deadline:
        listing = cast("dict[str, Any]", _mailpit_request("GET", "/api/v1/messages"))
        messages = cast("list[dict[str, Any]]", listing.get("messages", []))
        reset = next(
            (
                message
                for message in messages
                if message.get("Subject") == "Reset your LocalForge username"
            ),
            None,
        )
        if reset is not None:
            detail = cast(
                "dict[str, Any]",
                _mailpit_request("GET", f"/api/v1/message/{reset['ID']}"),
            )
            query = parse_qs(
                urlparse(
                    cast("str", detail["Text"]).split("Continue at ", maxsplit=1)[1].strip()
                ).query
            )
            link = {"account": query["account"][0], "token": query["token"][0]}
            assert cast("str", detail["HTML"])
            break
        time.sleep(MAILPIT_POLL_SECONDS)
    assert link is not None

    confirmed = client.post(
        "/api/v1/users/reset_username_confirm/",
        {**link, "new_username": f"username-smtp-after-{suffix}"},
        content_type="application/json",
        REMOTE_ADDR="192.0.2.241",
    )
    assert confirmed.status_code == HTTPStatus.NO_CONTENT

    deadline = time.monotonic() + MAILPIT_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        listing = cast("dict[str, Any]", _mailpit_request("GET", "/api/v1/messages"))
        messages = cast("list[dict[str, Any]]", listing.get("messages", []))
        notification = next(
            (
                message
                for message in messages
                if message.get("Subject") == "Your LocalForge username changed"
            ),
            None,
        )
        if notification is not None:
            detail = cast(
                "dict[str, Any]",
                _mailpit_request("GET", f"/api/v1/message/{notification['ID']}"),
            )
            text = cast("str", detail["Text"])
            assert account.email in str(notification["To"])
            assert link["token"] not in text
            assert account.username not in text
            assert f"username-smtp-after-{suffix}" not in text
            return
        time.sleep(MAILPIT_POLL_SECONDS)
    pytest.fail("Mailpit did not capture the username-change notification before timeout")
