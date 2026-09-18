"""Integration tests for account activation.

Exercises activation through the versioned account HTTP boundary and observes delivery through
the configured email backend, keeping assertions at the public request and captured-message seams.
"""

import http.client
import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from http import HTTPStatus
from importlib import import_module
from threading import Event, Lock
from typing import TYPE_CHECKING, Any, TypedDict, cast
from urllib.parse import parse_qs, quote, urlparse

import pytest
from django.conf import settings
from django.core import mail, signing
from django.db import DatabaseError, close_old_connections, connections, transaction
from django.db.backends.utils import CursorWrapper
from django.test import Client as DjangoClient
from django.test import override_settings
from freezegun import freeze_time
from kombu.exceptions import OperationalError

import accounts.user_profiles as user_profiles_module
from accounts.activation_tokens import ACTIVATION_SIGNING_SALT
from accounts.login_throttle import PostgresLoginThrottleStore
from accounts.models import ActivationToken, User
from accounts.tasks import send_activation_email
from config.api_errors import ErrorCode, ServiceUnavailable

if TYPE_CHECKING:
    from collections.abc import Callable

    from django.core.mail import EmailMultiAlternatives
    from django.test import Client

PASSWORD = "Correct-Horse-Battery-Staple-32"  # noqa: S105
SMTP_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
MAILPIT_TIMEOUT_SECONDS = 15
MAILPIT_POLL_SECONDS = 0.1
EXPECTED_ACCOUNT_COUNT = 2
EXPECTED_TOKEN_COUNT = 2
EXPECTED_ACCOUNT_STATE_COUNT = 3
EXPECTED_REGISTRATION_CANDIDATE_COUNT = 4
SECOND_LOCK_ORDER = 2
ACTIVATION_LIFETIME_SECONDS = 60
PUBLICATION_DELAY_SECONDS = 10
EXPIRED_PUBLICATION_DELAY_SECONDS = 61
pytestmark = pytest.mark.api_runtime


class MailpitAddress(TypedDict):
    """Address shape returned by Mailpit.

    Inherits from ``TypedDict`` and exposes only the mailbox field consumed by activation-loop
    assertions.

    Attributes:
        Address: Captured mailbox address.

    Members:
        None.
    """

    Address: str


class MailpitMessageSummary(TypedDict):
    """Message summary returned by the Mailpit listing.

    Inherits from ``TypedDict`` and exposes the identifier, subject, and recipients needed to find
    the activation email before its full content is fetched.

    Attributes:
        ID: Capture-service message identifier.
        Subject: Captured message subject.
        To: Captured recipient addresses.

    Members:
        None.
    """

    ID: str
    Subject: str
    To: list[MailpitAddress]


class MailpitMessages(TypedDict):
    """Message listing returned by Mailpit.

    Inherits from ``TypedDict`` and carries the captured summaries the polling loop inspects
    while waiting for SMTP delivery.

    Attributes:
        messages: Captured message summaries.

    Members:
        None.
    """

    messages: list[MailpitMessageSummary]


class MailpitMessageDetail(TypedDict):
    """Full message returned by Mailpit.

    Inherits from ``TypedDict`` and exposes both rendered bodies so the real SMTP seam can prove
    multipart activation content rather than relying on Django's local outbox.

    Attributes:
        Text: Captured plain-text body.
        HTML: Captured HTML body.

    Members:
        None.
    """

    Text: str
    HTML: str


class ActivationResendRace:
    """Coordinate activation before a competing resend account lock.

    Inherits nothing and wraps Django's cursor execution so a two-connection test can hold the
    first primary account lock and observe the second immediately before PostgreSQL blocks it.

    Attributes:
        activation_locked: Signal that activation owns the primary account row.
        resend_waiting: Signal that resend reached its competing account lock.
        release_activation: Signal allowing activation to commit.

    Members:
        __call__: Delegate SQL while coordinating the first two account row locks.
    """

    def __init__(self) -> None:
        """Initialize synchronization state.

        Creates independent events, a counter lock, and the unpatched cursor method used to execute
        every statement exactly once.

        Arguments:
            None.

        Returns:
            None.
        """
        self.activation_locked = Event()
        self.resend_waiting = Event()
        self.release_activation = Event()
        self._order_lock = Lock()
        self._lock_order = 0
        self._execute = CursorWrapper.execute

    def __call__(
        self,
        wrapper: CursorWrapper,
        sql: str,
        params: object = None,
    ) -> object:
        """Coordinate the first activation and resend account locks.

        Lets activation acquire the primary row before pausing it, then marks resend immediately
        before its competing lock blocks so the first transaction can be released deterministically.

        Arguments:
            wrapper: Django database cursor wrapper executing the statement.
            sql: SQL statement generated by the ORM.
            params: Optional statement parameters.

        Returns:
            Result from the delegated database statement.
        """
        is_account_lock = (
            'FROM "accounts_user"' in sql
            and "FOR UPDATE" in sql
            and sql.lstrip().upper().startswith("SELECT")
        )
        if not is_account_lock:
            return self._execute(wrapper, sql, cast("Any", params))

        with self._order_lock:
            self._lock_order += 1
            current_order = self._lock_order
        if current_order == 1:
            result = self._execute(wrapper, sql, cast("Any", params))
            self.activation_locked.set()
            assert self.release_activation.wait(10)
            return result
        if current_order == SECOND_LOCK_ORDER:
            self.resend_waiting.set()
        return self._execute(wrapper, sql, cast("Any", params))


class AccountLockPause:
    """Pause one chosen primary account lock before execution.

    Inherits nothing and counts account ``SELECT FOR UPDATE`` statements so concurrency tests can
    mutate token or account state immediately before a chosen lock obtains its authoritative row.

    Attributes:
        reached: Signal that the chosen account lock is about to execute.
        release: Signal allowing the chosen account lock to continue.

    Members:
        __call__: Delegate SQL while pausing the configured account lock.
    """

    def __init__(self, pause_order: int) -> None:
        """Configure which account lock should pause.

        Creates independent synchronization events and retains Django's original cursor execution
        method so every statement is delegated exactly once.

        Arguments:
            pause_order: One-based account-lock occurrence to pause.

        Returns:
            None.
        """
        self.reached = Event()
        self.release = Event()
        self._pause_order = pause_order
        self._order_lock = Lock()
        self._lock_order = 0
        self._execute = CursorWrapper.execute

    def __call__(
        self,
        wrapper: CursorWrapper,
        sql: str,
        params: object = None,
    ) -> object:
        """Pause the configured account lock before delegating it.

        Leaves unrelated SQL untouched and blocks only the selected account row lock until the
        coordinating test commits its competing state transition.

        Arguments:
            wrapper: Django database cursor wrapper executing the statement.
            sql: SQL statement generated by the ORM.
            params: Optional statement parameters.

        Returns:
            Result from the delegated database statement.
        """
        is_account_lock = (
            'FROM "accounts_user"' in sql
            and "FOR UPDATE" in sql
            and sql.lstrip().upper().startswith("SELECT")
        )
        if is_account_lock:
            with self._order_lock:
                self._lock_order += 1
                current_order = self._lock_order
            if current_order == self._pause_order:
                self.reached.set()
                assert self.release.wait(10)
        return self._execute(wrapper, sql, cast("Any", params))


def _post_activation(account_id: str, token: str) -> int:
    """Confirm activation on an independent primary connection.

    Uses the public HTTP seam while the cursor wrapper coordinates its account lock with resend,
    opening and closing the thread's independent primary connection around the request.

    Arguments:
        account_id: Account key carried by the activation link.
        token: Signed one-time activation value.

    Returns:
        Activation response status.
    """
    close_old_connections()
    try:
        result = DjangoClient().post(
            "/api/v1/users/",
            {"account": account_id, "token": token},
            content_type="application/json",
        )
        return result.status_code
    finally:
        close_old_connections()


def _run_activation_task(account_id: str, token: str) -> bool:
    """Run activation delivery on an independent primary connection.

    Calls the public Celery task seam while a cursor coordinator pauses its account locks, opening
    and closing the thread's database connection around execution.

    Arguments:
        account_id: Account key carried by the activation link.
        token: Signed one-time activation value.

    Returns:
        Whether the task delivered one message.
    """
    close_old_connections()
    try:
        return send_activation_email(account_id, token)
    finally:
        close_old_connections()


def _post_resend(email: str) -> int:
    """Request resend on an independent primary connection.

    Uses the public HTTP seam so throttle admission and issuance both participate in the race,
    opening and closing the thread's independent primary connection around the request.

    Arguments:
        email: Normalized account address submitted for resend.

    Returns:
        Resend response status.
    """
    close_old_connections()
    try:
        result = DjangoClient().post(
            "/api/v1/users/resend_activation/",
            {"email": email},
            content_type="application/json",
            REMOTE_ADDR="192.0.2.44",
        )
        return result.status_code
    finally:
        close_old_connections()


def _mailpit_request(method: str, path: str) -> object | None:
    """Call one Mailpit API path.

    Uses environment-specific host and web port settings so the same test reaches the profile
    service from host mode and from the one-off testing container.

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
    if method == "DELETE":
        return None
    return json.loads(payload) if payload else None


@pytest.fixture(name="_suppress_activation_delivery")
def suppress_activation_delivery(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep timing-window tests independent of eager email rendering.

    Replaces only the external task-publication boundary so a one-second primary-database window
    cannot elapse while the testing environment executes a background task inline.

    Arguments:
        monkeypatch: Fixture replacing the delayed task call.

    Returns:
        None.
    """

    def discard(
        *,
        args: tuple[str, str],
        expires: int,
    ) -> None:
        """Accept one publication without running its task.

        Preserves the callback and argument shape while removing SMTP and template duration from a
        test concerned only with authoritative admission time.

        Arguments:
            args: Account key and signed value the task would receive.
            expires: Remaining token lifetime the broker would enforce.

        Returns:
            None.
        """
        del args, expires

    monkeypatch.setattr(send_activation_email, "apply_async", discard)


def _registration_payload(username: str) -> dict[str, str]:
    """Build one valid registration body.

    Keeps the activation loop focused on delivery and confirmation while using the complete public
    registration contract established by Ticket 31.

    Arguments:
        username: Unique username used to derive the email address.

    Returns:
        Complete registration request body.
    """
    return {
        "username": username,
        "email": f"{username}@localforge.invalid",
        "password": PASSWORD,
        "password_confirm": PASSWORD,
    }


def _activation_fields(message: EmailMultiAlternatives) -> dict[str, str]:
    """Read activation fields from one captured message.

    Parses the sole URL rendered in the plain-text part so the test consumes the same account and
    token values an email client would hand to the activation form.

    Arguments:
        message: Multipart activation email captured by Django.

    Returns:
        Account and token fields accepted by the activation request.

    Raises:
        AssertionError: If the message does not contain exactly one usable activation link.
    """
    links = [word for word in message.body.split() if word.startswith("http")]
    assert len(links) == 1
    query = parse_qs(urlparse(links[0]).query)

    return {
        "account": query["account"][0],
        "token": query["token"][0],
    }


def _register_for_activation(client: Client, username: str) -> dict[str, str]:
    """Register one account and return its captured activation fields.

    Drives token issuance only through the public registration boundary so rejection tests never
    depend on a private generator or database representation.

    Arguments:
        client: Django test client issuing the registration.
        username: Unique username used to derive the email address.

    Returns:
        Account and token values parsed from the captured email.

    Raises:
        AssertionError: If registration or delivery fails.
    """
    response = client.post(
        "/api/v1/users/",
        _registration_payload(username),
        content_type="application/json",
    )
    assert response.status_code == HTTPStatus.CREATED
    assert len(mail.outbox) >= 1
    return _activation_fields(cast("EmailMultiAlternatives", mail.outbox[-1]))


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_registration_email_activates_once_through_the_existing_user_post(
    client: Client,
) -> None:
    """Activate a newly registered account exactly once.

    Registers through the public collection, consumes the captured multipart email, and posts its
    account-bound token back to the same route before replaying it.

    Arguments:
        client: Django test client issuing versioned requests.

    Returns:
        None.

    Raises:
        AssertionError: If delivery, activation, or single-use enforcement fails.
    """
    username = f"activation-{uuid.uuid4().hex}"

    registered = client.post(
        "/api/v1/users/",
        _registration_payload(username),
        content_type="application/json",
    )

    assert registered.status_code == HTTPStatus.CREATED
    assert len(mail.outbox) == 1

    message = cast("EmailMultiAlternatives", mail.outbox[0])
    fields = _activation_fields(message)
    activated = client.post(
        "/api/v1/users/",
        fields,
        content_type="application/json",
    )
    replayed = client.post(
        "/api/v1/users/",
        fields,
        content_type="application/json",
    )
    account = User.objects.using("default").get(username=username)
    token_record = ActivationToken.objects.using("default").get(account=account)
    replayed_payload = cast("dict[str, Any]", replayed.json())

    assert message.body.strip()
    assert len(message.alternatives) == 1
    assert str(message.alternatives[0][0]).strip()
    assert message.alternatives[0][1] == "text/html"
    assert PASSWORD not in message.body
    assert settings.SECRET_KEY not in message.body
    assert settings.JWT_SIGNING_KEY not in message.body
    assert activated.status_code == HTTPStatus.NO_CONTENT
    assert account.is_active is True
    assert str(token_record) == f"{account.pk}:{token_record.pk}"
    assert fields["token"] not in str(token_record)
    assert token_record.digest not in str(token_record)
    assert replayed.status_code == HTTPStatus.BAD_REQUEST
    assert replayed_payload["code"] == ErrorCode.ACTIVATION_TOKEN_USED


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_used_activation_token_stays_used_after_account_deletion(client: Client) -> None:
    """Preserve used-token classification after its account is deleted.

    Activates through the public route, deletes the account, and replays the same bearer so
    account-independent token state retains the required classification without exposing lifetime.

    Arguments:
        client: Django test client issuing versioned requests.

    Returns:
        None.

    Raises:
        AssertionError: If deletion collapses a used token into the foreign-token response.
    """
    fields = _register_for_activation(client, f"deleted-replay-{uuid.uuid4().hex}")
    activated = client.post(
        "/api/v1/users/",
        fields,
        content_type="application/json",
    )
    account = User.objects.using("default").get(pk=fields["account"])
    account.delete(using="default")

    replayed = client.post(
        "/api/v1/users/",
        fields,
        content_type="application/json",
    )

    assert activated.status_code == HTTPStatus.NO_CONTENT
    assert replayed.status_code == HTTPStatus.BAD_REQUEST
    assert cast("dict[str, Any]", replayed.json())["code"] == ErrorCode.ACTIVATION_TOKEN_USED
    token = ActivationToken.objects.using("default").get(subject_id=fields["account"])
    assert token.account is None


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_admin_activation_consumes_links_before_account_deletion(
    admin_client: Client,
    client: Client,
) -> None:
    """Preserve used-token classification after an administrator activates an account.

    Registers and resends through the public API, changes the account from inactive to active
    through Django administration, deletes it, and replays both formerly outstanding links.

    Arguments:
        admin_client: Authenticated administration client supplied by the test framework.
        client: Django test client issuing versioned account requests.

    Returns:
        None.

    Raises:
        AssertionError: If administration leaves a link outstanding or deletion loses used state.
    """
    username = f"admin-activation-{uuid.uuid4().hex}"
    first = _register_for_activation(client, username)
    resend = client.post(
        "/api/v1/users/resend_activation/",
        {"email": f"{username}@localforge.invalid"},
        content_type="application/json",
    )
    second = _activation_fields(cast("EmailMultiAlternatives", mail.outbox[-1]))
    account = User.objects.using("default").get(pk=first["account"])

    changed = admin_client.post(
        f"/admin/accounts/user/{account.pk}/change/",
        {
            "username": account.username,
            "email": account.email,
            "is_active": "on",
            "_save": "Save",
        },
    )
    account.refresh_from_db(using="default")
    unchanged = admin_client.post(
        f"/admin/accounts/user/{account.pk}/change/",
        {
            "username": account.username,
            "email": account.email,
            "is_active": "on",
            "_save": "Save",
        },
    )
    account.delete(using="default")
    replays = [
        client.post("/api/v1/users/", fields, content_type="application/json")
        for fields in (first, second)
    ]

    assert resend.status_code == HTTPStatus.ACCEPTED
    assert changed.status_code == HTTPStatus.FOUND
    assert unchanged.status_code == HTTPStatus.FOUND
    assert account.is_active is True
    assert [response.status_code for response in replays] == [HTTPStatus.BAD_REQUEST] * 2
    assert [cast("dict[str, Any]", response.json())["code"] for response in replays] == [
        ErrorCode.ACTIVATION_TOKEN_USED
    ] * 2
    records = ActivationToken.objects.using("default").filter(subject_id=first["account"])
    assert records.count() == EXPECTED_TOKEN_COUNT
    assert (
        records.filter(account__isnull=True, used_at__isnull=False).count() == EXPECTED_TOKEN_COUNT
    )


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_admin_email_change_invalidates_old_links_and_new_link_activates(
    admin_client: Client,
    client: Client,
) -> None:
    """Bind activation to the inactive account's current administration email.

    Registers an inactive account, changes its address through the public administration form,
    rejects the old link as used, and proves a resend to the replacement address can activate it.

    Arguments:
        admin_client: Authenticated administration client supplied by the test framework.
        client: Django test client issuing versioned account requests.

    Returns:
        None.

    Raises:
        AssertionError: If reassignment leaves the old link usable or blocks a replacement link.
    """
    username = f"admin-email-{uuid.uuid4().hex}"
    old_link = _register_for_activation(client, username)
    account = User.objects.using("default").get(pk=old_link["account"])
    replacement_email = f"replacement-{uuid.uuid4().hex}@localforge.invalid"

    changed = admin_client.post(
        f"/admin/accounts/user/{account.pk}/change/",
        {
            "username": account.username,
            "email": replacement_email,
            "_save": "Save",
        },
    )
    old_response = client.post(
        "/api/v1/users/",
        old_link,
        content_type="application/json",
    )
    account.refresh_from_db(using="default")
    mail.outbox.clear()
    resent = client.post(
        "/api/v1/users/resend_activation/",
        {"email": replacement_email},
        content_type="application/json",
    )
    new_link = _activation_fields(cast("EmailMultiAlternatives", mail.outbox[-1]))
    activated = client.post(
        "/api/v1/users/",
        new_link,
        content_type="application/json",
    )
    account.refresh_from_db(using="default")

    assert changed.status_code == HTTPStatus.FOUND
    assert old_response.status_code == HTTPStatus.BAD_REQUEST
    assert cast("dict[str, Any]", old_response.json())["code"] == ErrorCode.ACTIVATION_TOKEN_USED
    assert resent.status_code == HTTPStatus.ACCEPTED
    assert mail.outbox[-1].to == [replacement_email]
    assert activated.status_code == HTTPStatus.NO_CONTENT
    assert account.email == replacement_email
    assert account.is_active is True


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_unused_activation_token_becomes_foreign_after_account_deletion(
    client: Client,
) -> None:
    """Keep an unused orphan distinct from a consumed deletion tombstone.

    Deletes an inactive account before confirmation and submits its otherwise-valid bearer so the
    response remains foreign rather than revealing account lifetime or claiming prior use.

    Arguments:
        client: Django test client issuing versioned requests.

    Returns:
        None.

    Raises:
        AssertionError: If an unused orphan discloses deletion or collapses into used state.
    """
    fields = _register_for_activation(client, f"deleted-unused-{uuid.uuid4().hex}")
    account = User.objects.using("default").get(pk=fields["account"])
    account.delete(using="default")

    response = client.post(
        "/api/v1/users/",
        fields,
        content_type="application/json",
    )

    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert cast("dict[str, Any]", response.json())["code"] == ErrorCode.ACTIVATION_TOKEN_FOREIGN
    token = ActivationToken.objects.using("default").get(subject_id=fields["account"])
    assert token.account is None
    assert token.used_at is None


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_resend_is_identical_for_unknown_inactive_and_active_accounts(
    client: Client,
) -> None:
    """Hide account state while sending only to an inactive account.

    Submits unknown, inactive, and active addresses through the public resend route, comparing the
    complete accepted representations and observing that only the inactive inbox receives a link.

    Arguments:
        client: Django test client issuing versioned requests.

    Returns:
        None.

    Raises:
        AssertionError: If the response discloses state or an active account receives a link.
    """
    suffix = uuid.uuid4().hex
    inactive = User.objects.create_user(
        f"inactive-{suffix}",
        f"inactive-{suffix}@localforge.invalid",
        PASSWORD,
    )
    active = User.objects.create_user(
        f"active-{suffix}",
        f"active-{suffix}@localforge.invalid",
        PASSWORD,
        is_active=True,
    )

    responses = [
        client.post(
            "/api/v1/users/resend_activation/",
            {"email": address},
            content_type="application/json",
        )
        for address in (
            f"unknown-{suffix}@localforge.invalid",
            inactive.email,
            active.email,
        )
    ]

    assert [response.status_code for response in responses] == [HTTPStatus.ACCEPTED] * 3
    assert [response.json() for response in responses] == [
        {
            "detail": (
                "If an inactive account matches this address, an activation email will be sent."
            )
        }
    ] * 3
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == [inactive.email]


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_duplicate_registration_emails_only_the_matching_inactive_inbox(
    client: Client,
) -> None:
    """Keep duplicate registration responses equal while protecting unrelated inboxes.

    Repeats an inactive email, an active email, and an occupied username with a new address,
    observing identical registration responses and one link only to the inactive address owner.

    Arguments:
        client: Django test client issuing versioned requests.

    Returns:
        None.

    Raises:
        AssertionError: If duplicate handling discloses state or emails an unrelated address.
    """
    suffix = uuid.uuid4().hex
    inactive = User.objects.create_user(
        f"duplicate-inactive-{suffix}",
        f"duplicate-inactive-{suffix}@localforge.invalid",
        PASSWORD,
    )
    active = User.objects.create_user(
        f"duplicate-active-{suffix}",
        f"duplicate-active-{suffix}@localforge.invalid",
        PASSWORD,
        is_active=True,
    )
    requests = (
        _registration_payload(
            f"new-inactive-name-{suffix}",
        )
        | {"email": inactive.email.upper()},
        _registration_payload(
            f"new-active-name-{suffix}",
        )
        | {"email": active.email.upper()},
        _registration_payload(inactive.username.upper())
        | {"email": f"unrelated-{suffix}@localforge.invalid"},
    )

    responses = [
        client.post(
            "/api/v1/users/",
            body,
            content_type="application/json",
            REMOTE_ADDR=f"203.0.113.{index + 1}",
        )
        for index, body in enumerate(requests)
    ]

    assert [response.status_code for response in responses] == [HTTPStatus.CREATED] * 3
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == [inactive.email]
    assert User.objects.using("default").count() == EXPECTED_ACCOUNT_COUNT


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_activation_consumes_every_outstanding_link_for_the_account(client: Client) -> None:
    """Invalidate older links when any current link activates the account.

    Registers and resends to obtain two independently issued links, activates with the newer one,
    and verifies the older bearer value receives the stable used-token response.

    Arguments:
        client: Django test client issuing versioned requests.

    Returns:
        None.

    Raises:
        AssertionError: If an older outstanding link remains usable after activation.
    """
    username = f"outstanding-{uuid.uuid4().hex}"
    first = _register_for_activation(client, username)
    resend = client.post(
        "/api/v1/users/resend_activation/",
        {"email": f"{username}@localforge.invalid"},
        content_type="application/json",
    )
    second = _activation_fields(cast("EmailMultiAlternatives", mail.outbox[-1]))

    activated = client.post(
        "/api/v1/users/",
        second,
        content_type="application/json",
    )
    old_link = client.post(
        "/api/v1/users/",
        first,
        content_type="application/json",
    )

    assert resend.status_code == HTTPStatus.ACCEPTED
    assert activated.status_code == HTTPStatus.NO_CONTENT
    assert old_link.status_code == HTTPStatus.BAD_REQUEST
    assert cast("dict[str, Any]", old_link.json())["code"] == ErrorCode.ACTIVATION_TOKEN_USED
    assert (
        ActivationToken.objects.using("default")
        .filter(account_id=uuid.UUID(first["account"]), used_at__isnull=True)
        .count()
        == 0
    )


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.timeout(30)
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_activation_serializes_a_concurrent_resend_without_a_stale_link(
    client: Client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Let activation win the account lock before a concurrent resend.

    Holds activation after its primary account lock, starts resend independently, then releases
    activation so resend must re-read active state and publish only dummy work.

    Arguments:
        client: Django test client issuing initial registration.
        monkeypatch: Fixture capturing publication and synchronizing account row locks.

    Returns:
        None.

    Raises:
        AssertionError: If resend leaves an unused token or publishes a stale real account link.
    """
    published: list[tuple[str, str]] = []

    def capture(
        *,
        args: tuple[str, str],
        expires: int,
    ) -> None:
        """Capture activation task publication.

        Keeps queue execution outside the race while retaining enough payload identity to
        distinguish real account work from dummy scheduling.

        Arguments:
            args: Account key and signed activation token.
            expires: Remaining token lifetime the broker would enforce.

        Returns:
            None.
        """
        del expires
        published.append(args)

    monkeypatch.setattr(send_activation_email, "apply_async", capture)
    username = f"activation-resend-race-{uuid.uuid4().hex}"
    response = client.post(
        "/api/v1/users/",
        _registration_payload(username),
        content_type="application/json",
    )
    assert response.status_code == HTTPStatus.CREATED
    account_id, token = published.pop()

    race = ActivationResendRace()

    def synchronize(
        wrapper: CursorWrapper,
        sql: str,
        params: object = None,
    ) -> object:
        """Delegate one cursor statement through the race coordinator.

        Preserves descriptor binding for Django's cursor wrapper while keeping synchronization
        state in the dedicated coordinator object.

        Arguments:
            wrapper: Django database cursor wrapper executing the statement.
            sql: SQL statement generated by the ORM.
            params: Optional statement parameters.

        Returns:
            Result from the coordinated database statement.
        """
        return race(wrapper, sql, params)

    monkeypatch.setattr(CursorWrapper, "execute", synchronize)
    with ThreadPoolExecutor(max_workers=2) as executor:
        activation = executor.submit(_post_activation, account_id, token)
        assert race.activation_locked.wait(10)
        resend_request = executor.submit(
            _post_resend,
            f"{username}@localforge.invalid",
        )
        assert race.resend_waiting.wait(10)
        race.release_activation.set()
        activation_status = activation.result()
        resend_status = resend_request.result()

    account = User.objects.using("default").get(pk=account_id)
    token_records = ActivationToken.objects.using("default").filter(subject_id=account.pk)

    assert activation_status == HTTPStatus.NO_CONTENT
    assert resend_status == HTTPStatus.ACCEPTED
    assert account.is_active is True
    assert token_records.filter(used_at__isnull=True).count() == 0
    assert len(published) == 1
    assert published[0][0] != account_id
    assert mail.outbox == []


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.timeout(30)
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_activation_classifies_a_token_removed_after_preflight_as_foreign(
    client: Client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Contain token removal between confirmation preflight and account locking.

    Pauses the public activation request immediately before its account row lock, removes the token
    on another connection, and resumes so the response remains the stable foreign classification.

    Arguments:
        client: Django test client issuing initial registration.
        monkeypatch: Fixture synchronizing confirmation with token removal.

    Returns:
        None.

    Raises:
        AssertionError: If the race escapes as a server error or another token classification.
    """
    fields = _register_for_activation(client, f"removed-preflight-{uuid.uuid4().hex}")
    pause = AccountLockPause(1)

    def synchronize(
        wrapper: CursorWrapper,
        sql: str,
        params: object = None,
    ) -> object:
        """Delegate one cursor statement through the account-lock pause.

        Preserves Django cursor descriptor binding while the coordinator exposes the point between
        digest preflight and the primary account lock.

        Arguments:
            wrapper: Django database cursor wrapper executing the statement.
            sql: SQL statement generated by the ORM.
            params: Optional statement parameters.

        Returns:
            Result from the coordinated database statement.
        """
        return pause(wrapper, sql, params)

    monkeypatch.setattr(CursorWrapper, "execute", synchronize)
    with ThreadPoolExecutor(max_workers=1) as executor:
        activation = executor.submit(
            _post_activation,
            fields["account"],
            fields["token"],
        )
        assert pause.reached.wait(10)
        ActivationToken.objects.using("default").filter(
            subject_id=fields["account"],
        ).delete()
        pause.release.set()
        status = activation.result()

    assert status == HTTPStatus.BAD_REQUEST
    assert not User.objects.using("default").get(pk=fields["account"]).is_active


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_malformed_activation_is_identical_for_existing_and_unknown_accounts(
    client: Client,
) -> None:
    """Reject malformed input without disclosing account existence.

    Submits the same unauthenticated token shape with a real and a random account key, comparing
    every envelope field except the required per-request correlation identifier.

    Arguments:
        client: Django test client issuing versioned requests.

    Returns:
        None.

    Raises:
        AssertionError: If malformed classification or enumeration resistance fails.
    """
    fields = _register_for_activation(client, f"malformed-{uuid.uuid4().hex}")
    requests = (
        {"account": fields["account"], "token": "not-a-signed-token"},
        {"account": str(uuid.uuid4()), "token": "not-a-signed-token"},
    )
    responses = [
        client.post("/api/v1/users/", body, content_type="application/json") for body in requests
    ]
    payloads = [cast("dict[str, Any]", response.json()) for response in responses]
    comparable = [
        {key: value for key, value in payload.items() if key != "request_id"}
        for payload in payloads
    ]

    assert [response.status_code for response in responses] == [HTTPStatus.BAD_REQUEST] * 2
    assert comparable[0] == comparable[1]
    assert comparable[0]["code"] == ErrorCode.ACTIVATION_TOKEN_MALFORMED
    assert all(
        payload["request_id"] == response.headers["X-Request-ID"]
        for payload, response in zip(payloads, responses, strict=True)
    )


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_activation_token_bound_to_another_account_is_foreign(client: Client) -> None:
    """Reject a valid token paired with a different account.

    Registers two accounts through the public boundary and combines one token with the other's key,
    proving a correctly signed value cannot activate an account it was not issued for.

    Arguments:
        client: Django test client issuing versioned requests.

    Returns:
        None.

    Raises:
        AssertionError: If account binding is not enforced or either account is activated.
    """
    first = _register_for_activation(client, f"foreign-first-{uuid.uuid4().hex}")
    second = _register_for_activation(client, f"foreign-second-{uuid.uuid4().hex}")

    responses = [
        client.post(
            "/api/v1/users/",
            {"account": account, "token": first["token"]},
            content_type="application/json",
        )
        for account in (second["account"], str(uuid.uuid4()))
    ]
    payloads = [cast("dict[str, Any]", response.json()) for response in responses]

    assert [response.status_code for response in responses] == [HTTPStatus.BAD_REQUEST] * 2
    assert [payload["code"] for payload in payloads] == [ErrorCode.ACTIVATION_TOKEN_FOREIGN] * 2
    comparable = [
        {key: value for key, value in payload.items() if key != "request_id"}
        for payload in payloads
    ]
    assert comparable[0] == comparable[1]
    assert (
        User.objects.using("default")
        .filter(pk__in=(first["account"], second["account"]), is_active=True)
        .count()
        == 0
    )


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@override_settings(ACCOUNT_ACTIVATION_TOKEN_LIFETIME_SECONDS=60)
def test_activation_token_expiry_uses_frozen_time(client: Client) -> None:
    """Reject a token after its configured lifetime.

    Freezes issuance and confirmation on opposite sides of the exact boundary so the test proves
    timestamp enforcement without sleeping or depending on wall-clock scheduling.

    Arguments:
        client: Django test client issuing versioned requests.

    Returns:
        None.

    Raises:
        AssertionError: If an expired token activates its account or receives another error code.
    """
    with freeze_time("2026-09-17T00:00:00Z"):
        fields = _register_for_activation(client, f"expired-{uuid.uuid4().hex}")

    with freeze_time("2026-09-17T00:01:01Z"):
        response = client.post(
            "/api/v1/users/",
            fields,
            content_type="application/json",
        )

    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert cast("dict[str, Any]", response.json())["code"] == ErrorCode.ACTIVATION_TOKEN_EXPIRED
    assert User.objects.using("default").get(pk=fields["account"]).is_active is False


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@override_settings(ACCOUNT_ACTIVATION_TOKEN_LIFETIME_SECONDS=60)
def test_delayed_activation_task_rejects_an_expired_token(
    client: Client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject expired activation work before SMTP delivery.

    Issues through registration with queue execution suppressed, advances beyond the signed
    lifetime, and invokes the public task seam so delayed work cannot send a stale link.

    Arguments:
        client: Django test client issuing versioned requests.
        monkeypatch: Fixture capturing task publication without executing it.

    Returns:
        None.

    Raises:
        AssertionError: If delayed task execution sends an expired activation token.
    """
    published: list[tuple[str, str]] = []

    def capture(
        *,
        args: tuple[str, str],
        expires: int,
    ) -> None:
        """Capture one activation task publication.

        Keeps the bearer value at the queue seam without running delivery before the frozen clock
        advances beyond its lifetime.

        Arguments:
            args: Account key and signed one-time activation value.
            expires: Remaining token lifetime the broker would enforce.

        Returns:
            None.
        """
        del expires
        published.append(args)

    monkeypatch.setattr(send_activation_email, "apply_async", capture)
    with freeze_time("2026-09-17T00:00:00Z"):
        registered = client.post(
            "/api/v1/users/",
            _registration_payload(f"delayed-expiry-{uuid.uuid4().hex}"),
            content_type="application/json",
        )
    assert registered.status_code == HTTPStatus.CREATED
    assert len(published) == 1
    account_id, token = published[0]

    with freeze_time("2026-09-17T00:01:01Z"):
        delivered = send_activation_email(account_id, token)

    assert delivered is False
    assert mail.outbox == []


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.timeout(30)
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@override_settings(ACCOUNT_ACTIVATION_TOKEN_LIFETIME_SECONDS=60)
def test_claimed_activation_expiring_before_final_locks_sends_nothing(
    client: Client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject a claimed token that expires before final delivery validation.

    Issues at frozen time, starts delivery just before expiry, pauses before the final account lock,
    advances beyond maximum age, and resumes so no rendering or SMTP side effect occurs.

    Arguments:
        client: Django test client issuing initial registration.
        monkeypatch: Fixture capturing publication and pausing final delivery locks.

    Returns:
        None.

    Raises:
        AssertionError: If a durable claim remains sufficient after its token expires.
    """
    published: list[tuple[str, str]] = []

    def capture(
        *,
        args: tuple[str, str],
        expires: int,
    ) -> None:
        """Capture one activation task publication.

        Retains the bearer for controlled delayed execution without running eager delivery during
        registration.

        Arguments:
            args: Account key and signed activation token.
            expires: Remaining token lifetime supplied to Celery.

        Returns:
            None.
        """
        del expires
        published.append(args)

    monkeypatch.setattr(send_activation_email, "apply_async", capture)
    with freeze_time("2026-09-17T00:00:00Z"):
        response = client.post(
            "/api/v1/users/",
            _registration_payload(f"claimed-expiry-{uuid.uuid4().hex}"),
            content_type="application/json",
        )
    assert response.status_code == HTTPStatus.CREATED
    account_id, token = published[0]
    pause = AccountLockPause(SECOND_LOCK_ORDER)

    def synchronize(
        wrapper: CursorWrapper,
        sql: str,
        params: object = None,
    ) -> object:
        """Delegate one cursor statement through the final-lock pause.

        Preserves descriptor binding while exposing the point after the durable claim and before
        the last authoritative account and token locks.

        Arguments:
            wrapper: Django database cursor wrapper executing the statement.
            sql: SQL statement generated by the ORM.
            params: Optional statement parameters.

        Returns:
            Result from the coordinated database statement.
        """
        return pause(wrapper, sql, params)

    monkeypatch.setattr(CursorWrapper, "execute", synchronize)
    with (
        freeze_time("2026-09-17T00:00:59Z") as frozen_time,
        ThreadPoolExecutor(max_workers=1) as executor,
    ):
        delivery = executor.submit(_run_activation_task, account_id, token)
        assert pause.reached.wait(10)
        frozen_time.tick(delta=timedelta(seconds=2))
        pause.release.set()
        delivered = delivery.result()

    record = ActivationToken.objects.using("default").get(subject_id=account_id)

    assert delivered is False
    assert record.delivery_claimed_at is not None
    assert record.delivered_at is None
    assert mail.outbox == []


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_activation_task_rejects_malformed_foreign_used_and_active_state(
    client: Client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject every non-deliverable activation task state before SMTP.

    Captures one issued task, exercises malformed and foreign bearer shapes, activates the
    account, and invokes the original task again so used and active state suppresses delivery.

    Arguments:
        client: Django test client issuing versioned requests.
        monkeypatch: Fixture capturing registration publication.

    Returns:
        None.

    Raises:
        AssertionError: If any rejected task state reaches the email backend.
    """
    published: list[tuple[str, str]] = []

    def capture(
        *,
        args: tuple[str, str],
        expires: int,
    ) -> None:
        """Capture one activation publication.

        Retains the issued account and token while preventing eager delivery from claiming the
        record before each rejection state is exercised.

        Arguments:
            args: Account key and signed activation token.
            expires: Remaining token lifetime the broker would enforce.

        Returns:
            None.
        """
        del expires
        published.append(args)

    monkeypatch.setattr(send_activation_email, "apply_async", capture)
    response = client.post(
        "/api/v1/users/",
        _registration_payload(f"task-state-{uuid.uuid4().hex}"),
        content_type="application/json",
    )
    assert response.status_code == HTTPStatus.CREATED
    account_id, token = published[0]

    malformed = send_activation_email(account_id, "not-signed")
    foreign = send_activation_email(str(uuid.uuid4()), token)
    activated = client.post(
        "/api/v1/users/",
        {"account": account_id, "token": token},
        content_type="application/json",
    )
    used_and_active = send_activation_email(account_id, token)

    assert malformed is False
    assert foreign is False
    assert activated.status_code == HTTPStatus.NO_CONTENT
    assert used_and_active is False
    assert mail.outbox == []


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@override_settings(ACCOUNT_ACTIVATION_TOKEN_LIFETIME_SECONDS=ACTIVATION_LIFETIME_SECONDS)
def test_activation_publication_expires_with_the_remaining_token_lifetime(
    client: Client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Expire queued activation work when its signed bearer expires.

    Holds registration inside an outer transaction, advances the frozen clock before commit, and
    observes publication carrying only the token lifetime that remains at the queue boundary.

    Arguments:
        client: Django test client issuing versioned requests.
        monkeypatch: Fixture capturing Celery publication options.

    Returns:
        None.

    Raises:
        AssertionError: If publication omits expiry or grants work longer than the signed token.
    """
    published: list[tuple[tuple[str, str], int]] = []

    def capture(
        *,
        args: tuple[str, str],
        expires: int,
    ) -> None:
        """Capture one expiring activation publication.

        Records only the task arguments and broker expiry needed to compare the queue lifetime with
        the frozen signed-token lifetime.

        Arguments:
            args: Account key and signed activation token.
            expires: Whole seconds the broker may retain the task.

        Returns:
            None.
        """
        published.append((args, expires))

    monkeypatch.setattr(send_activation_email, "apply_async", capture)
    with (
        freeze_time("2026-09-17T00:00:00Z") as frozen,
        transaction.atomic(using="default"),
    ):
        response = client.post(
            "/api/v1/users/",
            _registration_payload(f"publish-expiry-{uuid.uuid4().hex}"),
            content_type="application/json",
        )
        assert published == []
        frozen.tick(PUBLICATION_DELAY_SECONDS)

    assert response.status_code == HTTPStatus.CREATED
    assert len(published) == 1
    assert published[0][1] == ACTIVATION_LIFETIME_SECONDS - PUBLICATION_DELAY_SECONDS


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@pytest.mark.parametrize(
    "publication_delay",
    [ACTIVATION_LIFETIME_SECONDS, EXPIRED_PUBLICATION_DELAY_SECONDS],
)
@override_settings(ACCOUNT_ACTIVATION_TOKEN_LIFETIME_SECONDS=ACTIVATION_LIFETIME_SECONDS)
def test_activation_publication_skips_work_with_no_token_lifetime(
    client: Client,
    monkeypatch: pytest.MonkeyPatch,
    publication_delay: int,
) -> None:
    """Do not enqueue activation work at or beyond bearer expiry.

    Holds registration until the signed lifetime is exhausted or exceeded, exercising both zero
    remaining duration and signed-expiry rejection at the after-commit publication boundary.

    Arguments:
        client: Django test client issuing versioned requests.
        monkeypatch: Fixture observing whether Celery publication occurs.
        publication_delay: Seconds elapsed before the registration transaction commits.

    Returns:
        None.

    Raises:
        AssertionError: If expired or zero-lifetime work reaches the broker.
    """
    published: list[tuple[str, str]] = []

    def capture(
        *,
        args: tuple[str, str],
        expires: int,
    ) -> None:
        """Capture an unexpected activation publication.

        Records the payload only if the callback incorrectly publishes after the signed token has
        no usable lifetime remaining.

        Arguments:
            args: Account key and signed activation token.
            expires: Remaining token lifetime supplied to Celery.

        Returns:
            None.
        """
        del expires
        published.append(args)

    monkeypatch.setattr(send_activation_email, "apply_async", capture)
    with (
        freeze_time("2026-09-17T00:00:00Z") as frozen,
        transaction.atomic(using="default"),
    ):
        response = client.post(
            "/api/v1/users/",
            _registration_payload(f"publish-exhausted-{publication_delay}-{uuid.uuid4().hex}"),
            content_type="application/json",
        )
        frozen.tick(publication_delay)

    assert response.status_code == HTTPStatus.CREATED
    assert published == []


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_activation_delivery_is_durably_claimed_at_most_once(
    client: Client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Send one activation token at most once across task redelivery.

    Captures registration publication, invokes the same task payload twice, and observes one SMTP
    message plus durable claim and success timestamps on the authoritative token record.

    Arguments:
        client: Django test client issuing versioned requests.
        monkeypatch: Fixture capturing publication before direct task execution.

    Returns:
        None.

    Raises:
        AssertionError: If duplicate task invocation sends twice or lacks durable delivery state.
    """
    published: list[tuple[str, str]] = []

    def capture(
        *,
        args: tuple[str, str],
        expires: int,
    ) -> None:
        """Capture one activation publication.

        Retains the real queue payload while preventing eager execution from claiming it before the
        duplicate-delivery assertions.

        Arguments:
            args: Account key and signed activation token.
            expires: Remaining token lifetime in seconds.

        Returns:
            None.
        """
        del expires
        published.append(args)

    monkeypatch.setattr(send_activation_email, "apply_async", capture)
    response = client.post(
        "/api/v1/users/",
        _registration_payload(f"delivery-claim-{uuid.uuid4().hex}"),
        content_type="application/json",
    )
    assert response.status_code == HTTPStatus.CREATED
    assert len(published) == 1
    account_id, token = published[0]

    outcomes = [
        send_activation_email(account_id, token),
        send_activation_email(account_id, token),
    ]
    record = ActivationToken.objects.using("default").get(
        account_id=uuid.UUID(account_id),
    )

    assert outcomes == [True, False]
    assert len(mail.outbox) == 1
    assert record.delivery_claimed_at is not None
    assert record.delivered_at is not None
    assert send_activation_email.app.conf.task_acks_late is True
    assert send_activation_email.autoretry_for == ()


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.timeout(30)
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@pytest.mark.parametrize("state_change", ["delete", "activate"])
def test_claimed_delivery_rechecks_account_state_before_smtp(
    client: Client,
    monkeypatch: pytest.MonkeyPatch,
    state_change: str,
) -> None:
    """Suppress claimed mail when account state changes before SMTP.

    Pauses a task before its second account lock, deletes or activates the account independently,
    and resumes so the committed claim cannot authorize stale delivery.

    Arguments:
        client: Django test client issuing initial registration.
        monkeypatch: Fixture capturing publication and pausing task state validation.
        state_change: Account transition committed after the durable delivery claim.

    Returns:
        None.

    Raises:
        AssertionError: If a claimed task sends after deletion or activation.
    """
    published: list[tuple[str, str]] = []

    def capture(
        *,
        args: tuple[str, str],
        expires: int,
    ) -> None:
        """Capture initial activation publication.

        Prevents eager task execution while retaining the exact payload used by the independently
        synchronized delivery worker.

        Arguments:
            args: Account key and signed activation token.
            expires: Remaining token lifetime supplied to Celery.

        Returns:
            None.
        """
        del expires
        published.append(args)

    monkeypatch.setattr(send_activation_email, "apply_async", capture)
    response = client.post(
        "/api/v1/users/",
        _registration_payload(f"claimed-state-{state_change}-{uuid.uuid4().hex}"),
        content_type="application/json",
    )
    assert response.status_code == HTTPStatus.CREATED
    account_id, token = published[0]
    pause = AccountLockPause(SECOND_LOCK_ORDER)

    def synchronize(
        wrapper: CursorWrapper,
        sql: str,
        params: object = None,
    ) -> object:
        """Delegate one cursor statement through the account-lock pause.

        Preserves descriptor binding while the task commits its claim and pauses before the second
        authoritative account-state read.

        Arguments:
            wrapper: Django database cursor wrapper executing the statement.
            sql: SQL statement generated by the ORM.
            params: Optional statement parameters.

        Returns:
            Result from the coordinated database statement.
        """
        return pause(wrapper, sql, params)

    monkeypatch.setattr(CursorWrapper, "execute", synchronize)
    with ThreadPoolExecutor(max_workers=1) as executor:
        delivery = executor.submit(_run_activation_task, account_id, token)
        assert pause.reached.wait(10)
        accounts = User.objects.using("default").filter(pk=account_id)
        if state_change == "delete":
            accounts.delete()
        else:
            accounts.update(is_active=True)
        pause.release.set()
        delivered = delivery.result()

    record = ActivationToken.objects.using("default").get(subject_id=account_id)

    assert delivered is False
    assert record.delivery_claimed_at is not None
    assert record.delivered_at is None
    assert mail.outbox == []


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.timeout(30)
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_admin_email_change_serializes_before_claimed_delivery(
    admin_client: Client,
    client: Client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Suppress an old-address delivery when administration wins the final account lock.

    Pauses a worker after its durable claim, changes the inactive address through administration,
    and resumes delivery so the consumed bearer cannot render or send after reassignment.

    Arguments:
        admin_client: Authenticated administration client supplied by the test framework.
        client: Django test client issuing initial registration and old-link confirmation.
        monkeypatch: Fixture capturing publication and pausing final task validation.

    Returns:
        None.

    Raises:
        AssertionError: If delivery sends an old-address link or the bearer remains usable.
    """
    published: list[tuple[str, str]] = []

    def capture(
        *,
        args: tuple[str, str],
        expires: int,
    ) -> None:
        """Capture initial activation publication.

        Retains the exact worker payload while preventing eager delivery before the account-lock
        ordering is coordinated.

        Arguments:
            args: Account key and signed activation token.
            expires: Remaining token lifetime supplied to Celery.

        Returns:
            None.
        """
        del expires
        published.append(args)

    monkeypatch.setattr(send_activation_email, "apply_async", capture)
    username = f"admin-delivery-race-{uuid.uuid4().hex}"
    registered = client.post(
        "/api/v1/users/",
        _registration_payload(username),
        content_type="application/json",
    )
    assert registered.status_code == HTTPStatus.CREATED
    account_id, token = published[0]
    account = User.objects.using("default").get(pk=account_id)
    replacement_email = f"race-replacement-{uuid.uuid4().hex}@localforge.invalid"
    pause = AccountLockPause(SECOND_LOCK_ORDER)

    def synchronize(
        wrapper: CursorWrapper,
        sql: str,
        params: object = None,
    ) -> object:
        """Delegate one cursor statement through the final-lock pause.

        Preserves descriptor binding while exposing the interval between the worker's durable claim
        and its final authoritative account and token locks.

        Arguments:
            wrapper: Django database cursor wrapper executing the statement.
            sql: SQL statement generated by the ORM.
            params: Optional statement parameters.

        Returns:
            Result from the coordinated database statement.
        """
        return pause(wrapper, sql, params)

    monkeypatch.setattr(CursorWrapper, "execute", synchronize)
    with ThreadPoolExecutor(max_workers=1) as executor:
        delivery = executor.submit(_run_activation_task, account_id, token)
        assert pause.reached.wait(10)
        changed = admin_client.post(
            f"/admin/accounts/user/{account.pk}/change/",
            {
                "username": account.username,
                "email": replacement_email,
                "_save": "Save",
            },
        )
        pause.release.set()
        delivered = delivery.result()

    old_link = client.post(
        "/api/v1/users/",
        {"account": account_id, "token": token},
        content_type="application/json",
    )
    account.refresh_from_db(using="default")
    record = ActivationToken.objects.using("default").get(subject_id=account_id)

    assert changed.status_code == HTTPStatus.FOUND
    assert delivered is False
    assert mail.outbox == []
    assert account.email == replacement_email
    assert account.is_active is False
    assert record.delivery_claimed_at is not None
    assert record.delivered_at is None
    assert record.used_at is not None
    assert old_link.status_code == HTTPStatus.BAD_REQUEST
    assert cast("dict[str, Any]", old_link.json())["code"] == ErrorCode.ACTIVATION_TOKEN_USED


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_failed_activation_delivery_recovers_only_through_resend(
    client: Client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep a failed delivery claimed and recover with a newly issued token.

    Forces the first token's SMTP attempt to fail, proves redelivery cannot send it, then requests
    resend so a distinct token provides the safe recovery path and exactly one message is emitted.

    Arguments:
        client: Django test client issuing versioned requests.
        monkeypatch: Fixture capturing publication and inducing SMTP failure.

    Returns:
        None.

    Raises:
        AssertionError: If a failed claim retries delivery or resend cannot recover.
    """
    published: list[tuple[str, str]] = []

    def capture(
        *,
        args: tuple[str, str],
        expires: int,
    ) -> None:
        """Capture initial registration publication.

        Prevents eager delivery so the test can induce failure at the first real SMTP attempt,
        retaining the exact task payload that resend recovery must supersede.

        Arguments:
            args: Account key and signed activation token.
            expires: Remaining token lifetime the broker would enforce.

        Returns:
            None.
        """
        del expires
        published.append(args)

    with monkeypatch.context() as publication_patch:
        publication_patch.setattr(send_activation_email, "apply_async", capture)
        username = f"delivery-failure-{uuid.uuid4().hex}"
        response = client.post(
            "/api/v1/users/",
            _registration_payload(username),
            content_type="application/json",
        )
    assert response.status_code == HTTPStatus.CREATED
    account_id, token = published[0]

    def fail_smtp(_message: object, *, fail_silently: bool = False) -> int:
        """Reject one SMTP send.

        Represents transport failure at the external email boundary while retaining Django's
        backend call shape.

        Arguments:
            _message: Multipart message attempting delivery.
            fail_silently: Whether the caller requested contained failure.

        Returns:
            Never returns.

        Raises:
            OSError: Always, representing an unavailable SMTP transport.
        """
        del fail_silently
        raise OSError

    with monkeypatch.context() as smtp_patch:
        smtp_patch.setattr("django.core.mail.EmailMultiAlternatives.send", fail_smtp)
        failed = send_activation_email(account_id, token)

    redelivered = send_activation_email(account_id, token)
    failed_record = ActivationToken.objects.using("default").get(
        subject_id=uuid.UUID(account_id),
    )
    resent = client.post(
        "/api/v1/users/resend_activation/",
        {"email": f"{username}@localforge.invalid"},
        content_type="application/json",
    )

    assert failed is False
    assert redelivered is False
    assert failed_record.delivery_claimed_at is not None
    assert failed_record.delivered_at is None
    assert resent.status_code == HTTPStatus.ACCEPTED
    assert len(mail.outbox) == 1


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_rolled_back_registration_never_publishes_or_sends_activation(
    client: Client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep activation delivery behind the registration commit.

    Wraps the public request in an outer transaction and rolls it back after the accepted
    response, observing task publication before and after rollback and the resulting account state.

    Arguments:
        client: Django test client issuing the versioned request.
        monkeypatch: Fixture replacing only the external task-publication boundary.

    Returns:
        None.

    Raises:
        AssertionError: If publication runs before commit or survives rollback.
    """
    published: list[tuple[str, str]] = []

    def record(
        *,
        args: tuple[str, str],
        expires: int,
    ) -> None:
        """Record an attempted task publication.

        Stands in for the queue boundary so the test can distinguish callback registration from
        execution without reaching into Django's transaction callback storage.

        Arguments:
            args: Account key and signed value the task would receive.
            expires: Remaining token lifetime the broker would enforce.

        Returns:
            None.
        """
        del expires
        published.append(args)

    monkeypatch.setattr(send_activation_email, "apply_async", record)
    username = f"rollback-{uuid.uuid4().hex}"

    def register_then_roll_back() -> None:
        """Issue the request and abort its surrounding transaction.

        Keeps the exception assertion outside the transactional work while preserving the public
        request and pre-commit publication observation inside it.

        Arguments:
            None.

        Returns:
            Never returns.

        Raises:
            RuntimeError: Always, to force the surrounding transaction to roll back.
        """
        message = "roll back activation"
        with transaction.atomic(using="default"):
            response = client.post(
                "/api/v1/users/",
                _registration_payload(username),
                content_type="application/json",
            )
            assert response.status_code == HTTPStatus.CREATED
            assert published == []
            raise RuntimeError(message)

    with pytest.raises(RuntimeError, match="roll back activation"):
        register_then_roll_back()

    assert published == []
    assert mail.outbox == []
    assert not User.objects.using("default").filter(username=username).exists()


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.usefixtures("_suppress_activation_delivery")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_resend_account_throttle_is_shared_across_addresses_and_recovers(
    client: Client,
) -> None:
    """Limit one inbox across callers and recover after the exact window.

    Sends one inactive account from two client addresses around a one-second account window,
    proving the primary-backed identity is shared and later admits another task.

    Arguments:
        client: Django test client issuing versioned requests.

    Returns:
        None.

    Raises:
        AssertionError: If account admission is per caller, lacks Retry-After, or never recovers.
    """
    suffix = uuid.uuid4().hex
    account = User.objects.create_user(
        f"account-limit-{suffix}",
        f"account-limit-{suffix}@localforge.invalid",
        PASSWORD,
    )

    with override_settings(
        ACCOUNT_ACTIVATION_RESEND_ADDRESS_THROTTLE_RATE="100/minute",
        ACCOUNT_ACTIVATION_RESEND_ACCOUNT_THROTTLE_RATE="1/second",
    ):
        first = client.post(
            "/api/v1/users/resend_activation/",
            {"email": account.email},
            content_type="application/json",
            REMOTE_ADDR="192.0.2.31",
        )
        throttled = client.post(
            "/api/v1/users/resend_activation/",
            {"email": account.email.upper()},
            content_type="application/json",
            REMOTE_ADDR="192.0.2.32",
        )
        time.sleep(1.1)
        recovered = client.post(
            "/api/v1/users/resend_activation/",
            {"email": account.email},
            content_type="application/json",
            REMOTE_ADDR="192.0.2.32",
        )

    assert first.status_code == HTTPStatus.ACCEPTED
    assert throttled.status_code == HTTPStatus.TOO_MANY_REQUESTS
    assert int(throttled.headers["Retry-After"]) >= 1
    assert cast("dict[str, Any]", throttled.json())["code"] == ErrorCode.THROTTLED
    assert recovered.status_code == HTTPStatus.ACCEPTED
    assert mail.outbox == []


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.usefixtures("_suppress_activation_delivery")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_resend_address_throttle_spans_accounts_and_recovers(
    client: Client,
) -> None:
    """Limit one caller across submitted accounts and recover after the window.

    Sends distinct unknown addresses from one client around a one-second address window, proving
    changing the requested inbox cannot bypass primary-backed admission.

    Arguments:
        client: Django test client issuing versioned requests.

    Returns:
        None.

    Raises:
        AssertionError: If address admission is per account or never recovers.
    """
    remote_address = "192.0.2.33"
    suffix = uuid.uuid4().hex

    with override_settings(
        ACCOUNT_ACTIVATION_RESEND_ADDRESS_THROTTLE_RATE="1/second",
        ACCOUNT_ACTIVATION_RESEND_ACCOUNT_THROTTLE_RATE="100/minute",
    ):
        first = client.post(
            "/api/v1/users/resend_activation/",
            {"email": f"unknown-first-{suffix}@localforge.invalid"},
            content_type="application/json",
            REMOTE_ADDR=remote_address,
        )
        throttled = client.post(
            "/api/v1/users/resend_activation/",
            {"email": f"unknown-second-{suffix}@localforge.invalid"},
            content_type="application/json",
            REMOTE_ADDR=remote_address,
        )
        time.sleep(1.1)
        recovered = client.post(
            "/api/v1/users/resend_activation/",
            {"email": f"unknown-third-{suffix}@localforge.invalid"},
            content_type="application/json",
            REMOTE_ADDR=remote_address,
        )

    assert first.status_code == HTTPStatus.ACCEPTED
    assert throttled.status_code == HTTPStatus.TOO_MANY_REQUESTS
    assert recovered.status_code == HTTPStatus.ACCEPTED
    assert mail.outbox == []


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.usefixtures("_suppress_activation_delivery")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_unknown_resend_admission_constrains_the_later_registered_account(
    client: Client,
) -> None:
    """Keep one normalized-email window across account creation.

    Admits an unknown address, creates its account, and retries from another client address so the
    earlier email-bound request must still constrain the now-resolved account.

    Arguments:
        client: Django test client issuing versioned requests.

    Returns:
        None.

    Raises:
        AssertionError: If account creation changes the authoritative email admission identity.
    """
    suffix = uuid.uuid4().hex
    email = f"stable-resend-{suffix}@localforge.invalid"

    with override_settings(
        ACCOUNT_ACTIVATION_RESEND_ADDRESS_THROTTLE_RATE="100/minute",
        ACCOUNT_ACTIVATION_RESEND_ACCOUNT_THROTTLE_RATE="1/minute",
    ):
        unknown = client.post(
            "/api/v1/users/resend_activation/",
            {"email": email.upper()},
            content_type="application/json",
            REMOTE_ADDR="192.0.2.41",
        )
        User.objects.create_user(f"stable-resend-{suffix}", email, PASSWORD)
        registered = client.post(
            "/api/v1/users/resend_activation/",
            {"email": email},
            content_type="application/json",
            REMOTE_ADDR="192.0.2.42",
        )

    assert unknown.status_code == HTTPStatus.ACCEPTED
    assert registered.status_code == HTTPStatus.TOO_MANY_REQUESTS
    assert cast("dict[str, Any]", registered.json())["code"] == ErrorCode.THROTTLED


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.usefixtures("_suppress_activation_delivery")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_resend_account_admission_survives_an_email_change(client: Client) -> None:
    """Keep the immutable account window when its normalized email changes.

    Admits one inactive account, changes its address, and retries from another caller so the new
    email bucket remains constrained by the atomically charged account identity.

    Arguments:
        client: Django test client issuing versioned requests.

    Returns:
        None.

    Raises:
        AssertionError: If changing email bypasses immutable account admission.
    """
    suffix = uuid.uuid4().hex
    account = User.objects.create_user(
        f"immutable-resend-{suffix}",
        f"immutable-resend-{suffix}@localforge.invalid",
        PASSWORD,
    )

    with override_settings(
        ACCOUNT_ACTIVATION_RESEND_ADDRESS_THROTTLE_RATE="100/minute",
        ACCOUNT_ACTIVATION_RESEND_ACCOUNT_THROTTLE_RATE="1/minute",
    ):
        first = client.post(
            "/api/v1/users/resend_activation/",
            {"email": account.email},
            content_type="application/json",
            REMOTE_ADDR="192.0.2.45",
        )
        account.email = f"immutable-resend-updated-{suffix}@localforge.invalid"
        account.save(using="default", update_fields=("email", "updated_at"))
        changed = client.post(
            "/api/v1/users/resend_activation/",
            {"email": account.email},
            content_type="application/json",
            REMOTE_ADDR="192.0.2.46",
        )

    assert first.status_code == HTTPStatus.ACCEPTED
    assert changed.status_code == HTTPStatus.TOO_MANY_REQUESTS
    assert cast("dict[str, Any]", changed.json())["code"] == ErrorCode.THROTTLED


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.usefixtures("_suppress_activation_delivery")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_resend_metadata_methods_do_not_consume_post_quota(client: Client) -> None:
    """Record resend admission for POST only.

    Calls every non-POST method exposed or rejected by the fixed route before the sole admitted
    POST, proving metadata and method errors cannot consume its client or email windows.

    Arguments:
        client: Django test client issuing versioned requests.

    Returns:
        None.

    Raises:
        AssertionError: If a non-POST request consumes authoritative resend capacity.
    """
    email = f"post-only-{uuid.uuid4().hex}@localforge.invalid"

    with override_settings(
        ACCOUNT_ACTIVATION_RESEND_ADDRESS_THROTTLE_RATE="1/minute",
        ACCOUNT_ACTIVATION_RESEND_ACCOUNT_THROTTLE_RATE="1/minute",
    ):
        responses = (
            client.get("/api/v1/users/resend_activation/"),
            client.head("/api/v1/users/resend_activation/"),
            client.options("/api/v1/users/resend_activation/"),
            client.put(
                "/api/v1/users/resend_activation/",
                {"email": email},
                content_type="application/json",
            ),
        )
        posted = client.post(
            "/api/v1/users/resend_activation/",
            {"email": email},
            content_type="application/json",
        )

    assert [response.status_code for response in responses] == [
        HTTPStatus.METHOD_NOT_ALLOWED,
        HTTPStatus.METHOD_NOT_ALLOWED,
        HTTPStatus.OK,
        HTTPStatus.METHOD_NOT_ALLOWED,
    ]
    assert posted.status_code == HTTPStatus.ACCEPTED


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.usefixtures("_suppress_activation_delivery")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@pytest.mark.parametrize(
    "invalid_body_kind",
    ["undeclared", "missing", "non-object", "invalid-email"],
)
def test_invalid_resend_body_does_not_consume_quota(
    client: Client,
    invalid_body_kind: str,
) -> None:
    """Leave resend admission unchanged unless the complete body is valid.

    Submits one invalid body before a valid POST from the same caller under one-event limits,
    preserving correlated validation errors without charging address, email, or account admission.

    Arguments:
        client: Django test client issuing versioned requests.
        invalid_body_kind: Invalid complete-body shape submitted before the valid request.

    Returns:
        None.

    Raises:
        AssertionError: If invalid input is throttled or consumes the following valid request.
    """
    valid_email = f"valid-after-invalid-{uuid.uuid4().hex}@localforge.invalid"
    invalid_bodies: dict[str, object] = {
        "undeclared": {"email": valid_email, "is_active": True},
        "missing": {},
        "non-object": [],
        "invalid-email": {"email": "not-an-address"},
    }
    with override_settings(
        ACCOUNT_ACTIVATION_RESEND_ADDRESS_THROTTLE_RATE="1/minute",
        ACCOUNT_ACTIVATION_RESEND_ACCOUNT_THROTTLE_RATE="1/minute",
    ):
        invalid = client.post(
            "/api/v1/users/resend_activation/",
            json.dumps(invalid_bodies[invalid_body_kind]),
            content_type="application/json",
            REMOTE_ADDR="192.0.2.43",
        )
        valid = client.post(
            "/api/v1/users/resend_activation/",
            {"email": valid_email},
            content_type="application/json",
            REMOTE_ADDR="192.0.2.43",
        )

    assert invalid.status_code == HTTPStatus.BAD_REQUEST
    assert cast("dict[str, Any]", invalid.json())["code"] == ErrorCode.VALIDATION_ERROR
    assert cast("dict[str, Any]", invalid.json())["request_id"] == invalid.headers["X-Request-ID"]
    assert valid.status_code == HTTPStatus.ACCEPTED


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_duplicate_registration_uses_shared_activation_mail_admission(
    client: Client,
) -> None:
    """Limit duplicate inactive registration across distributed callers.

    Repeats one existing inactive email from separate client addresses under a one-message window,
    preserving identical registration success while only the admitted request sends real mail.

    Arguments:
        client: Django test client issuing versioned requests.

    Returns:
        None.

    Raises:
        AssertionError: If duplicate registration bypasses activation-mail admission or discloses
            it.
    """
    suffix = uuid.uuid4().hex
    account = User.objects.create_user(
        f"duplicate-admission-{suffix}",
        f"duplicate-admission-{suffix}@localforge.invalid",
        PASSWORD,
    )
    payload = _registration_payload(f"replacement-{suffix}") | {"email": account.email}

    with override_settings(
        USER_REGISTRATION_ADDRESS_THROTTLE_RATE="100/minute",
        ACCOUNT_ACTIVATION_RESEND_ACCOUNT_THROTTLE_RATE="1/minute",
    ):
        responses = [
            client.post(
                "/api/v1/users/",
                payload,
                content_type="application/json",
                REMOTE_ADDR=f"198.51.100.{index}",
            )
            for index in (41, 42)
        ]

    assert [response.status_code for response in responses] == [HTTPStatus.CREATED] * 2
    assert [response.json() for response in responses] == [
        {"username": payload["username"], "email": payload["email"]}
    ] * 2
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == [account.email]


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.usefixtures("_suppress_activation_delivery")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_unknown_resend_constrains_later_duplicate_registration(client: Client) -> None:
    """Carry unknown-email admission into duplicate registration.

    Admits resend before the account exists, creates it, and repeats registration from another
    address so the shared email window suppresses real activation mail without disclosure.

    Arguments:
        client: Django test client issuing versioned requests.

    Returns:
        None.

    Raises:
        AssertionError: If duplicate registration ignores earlier unknown-email admission.
    """
    suffix = uuid.uuid4().hex
    email = f"unknown-duplicate-{suffix}@localforge.invalid"

    with override_settings(
        USER_REGISTRATION_ADDRESS_THROTTLE_RATE="100/minute",
        ACCOUNT_ACTIVATION_RESEND_ADDRESS_THROTTLE_RATE="100/minute",
        ACCOUNT_ACTIVATION_RESEND_ACCOUNT_THROTTLE_RATE="1/minute",
    ):
        unknown = client.post(
            "/api/v1/users/resend_activation/",
            {"email": email},
            content_type="application/json",
            REMOTE_ADDR="198.51.100.43",
        )
        User.objects.create_user(f"unknown-duplicate-{suffix}", email, PASSWORD)
        payload = _registration_payload(f"unknown-duplicate-replacement-{suffix}") | {
            "email": email
        }
        duplicate = client.post(
            "/api/v1/users/",
            payload,
            content_type="application/json",
            REMOTE_ADDR="198.51.100.44",
        )

    assert unknown.status_code == HTTPStatus.ACCEPTED
    assert duplicate.status_code == HTTPStatus.CREATED
    assert duplicate.json() == {"username": payload["username"], "email": email}
    assert mail.outbox == []


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_duplicate_registration_hides_activation_admission_outage(
    client: Client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Preserve duplicate registration success when mail admission is unavailable.

    Leaves the registration-address decision operational while failing only the shared activation
    email and account buckets, proving the response stays indistinguishable and no real mail issues.

    Arguments:
        client: Django test client issuing versioned requests.
        monkeypatch: Fixture failing the activation-mail admission boundary.

    Returns:
        None.

    Raises:
        AssertionError: If admission loss changes the public success or sends real mail.
    """
    suffix = uuid.uuid4().hex
    account = User.objects.create_user(
        f"duplicate-outage-{suffix}",
        f"duplicate-outage-{suffix}@localforge.invalid",
        PASSWORD,
    )
    original_admit = PostgresLoginThrottleStore.admit

    def fail_activation_mail(
        store: PostgresLoginThrottleStore,
        rules: tuple[object, ...],
        *,
        member: str,
    ) -> object:
        """Fail only activation-mail admission.

        Delegates registration-address admission and raises when duplicate handling reaches its
        email or account security boundary.

        Arguments:
            store: Primary-backed throttle store receiving the rules.
            rules: Rolling-window dimensions being admitted.
            member: Opaque correlated admission identifier.

        Returns:
            Registration-address admission result.

        Raises:
            DatabaseError: If the rules belong to activation mail.
        """
        if any(getattr(rule, "key", "").startswith("activation-mail-") for rule in rules):
            raise DatabaseError
        return original_admit(store, cast("Any", rules), member=member)

    monkeypatch.setattr(PostgresLoginThrottleStore, "admit", fail_activation_mail)
    payload = _registration_payload(f"duplicate-outage-replacement-{suffix}") | {
        "email": account.email
    }

    response = client.post(
        "/api/v1/users/",
        payload,
        content_type="application/json",
    )

    assert response.status_code == HTTPStatus.CREATED
    assert response.json() == {"username": payload["username"], "email": payload["email"]}
    assert mail.outbox == []
    assert not ActivationToken.objects.using("default").filter(account=account).exists()


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_resend_admission_failure_is_a_correlated_service_unavailable(
    client: Client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail closed when authoritative resend admission is unavailable.

    Replaces the primary-backed admission boundary while leaving routing, middleware, and response
    handling real, proving no resend task is accepted without a shared decision.

    Arguments:
        client: Django test client issuing the versioned request.
        monkeypatch: Fixture inducing authoritative admission loss.

    Returns:
        None.

    Raises:
        AssertionError: If the request fails open or loses correlation.
    """

    def fail_admission(
        _store: PostgresLoginThrottleStore,
        _rules: object,
        *,
        member: str,
    ) -> None:
        """Raise one authoritative admission outage.

        Represents loss of primary-backed security state without replacing the endpoint or shared
        error handler that must contain it.

        Arguments:
            _store: Primary-backed throttle store receiving the request.
            _rules: Address and account rolling-window rules.
            member: Opaque correlated admission member.

        Returns:
            Never returns.

        Raises:
            DatabaseError: Always, representing unavailable authoritative state.
        """
        del member
        raise DatabaseError

    monkeypatch.setattr(PostgresLoginThrottleStore, "admit", fail_admission)

    response = client.post(
        "/api/v1/users/resend_activation/",
        {"email": f"outage-{uuid.uuid4().hex}@localforge.invalid"},
        content_type="application/json",
    )
    payload = cast("dict[str, Any]", response.json())

    assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert payload["code"] == ErrorCode.SERVICE_UNAVAILABLE
    assert payload["request_id"] == response.headers["X-Request-ID"]
    assert mail.outbox == []


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_resend_token_write_failure_is_identical_for_every_account_state(
    client: Client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hide inactive existence when real resend token persistence fails.

    Rejects only activation-token inserts while requesting unknown, active, and inactive addresses
    through the public route, comparing accepted responses and observing equivalent dummy work.

    Arguments:
        client: Django test client issuing versioned resend requests.
        monkeypatch: Fixture capturing the task-publication boundary.

    Returns:
        None.

    Raises:
        AssertionError: If one account state differs or leaves token or mail state behind.
    """
    suffix = uuid.uuid4().hex
    inactive = User.objects.create_user(
        f"inactive-write-failure-{suffix}",
        f"inactive-write-failure-{suffix}@localforge.invalid",
        PASSWORD,
    )
    active = User.objects.create_user(
        f"active-write-failure-{suffix}",
        f"active-write-failure-{suffix}@localforge.invalid",
        PASSWORD,
        is_active=True,
    )
    published: list[tuple[str, str]] = []

    def capture(
        *,
        args: tuple[str, str],
        expires: int,
    ) -> None:
        """Capture state-independent resend publication.

        Records queue payload identities without executing delivery, allowing every accepted path
        to be compared without an SMTP side effect.

        Arguments:
            args: Account-shaped key and signed activation token.
            expires: Remaining token lifetime supplied to Celery.

        Returns:
            None.
        """
        del expires
        published.append(args)

    def fail_token_insert(execute: object, sql: object, *arguments: object) -> object:
        """Reject only real activation-token persistence.

        Preserves lookup, admission, savepoint, and dummy scheduling SQL while making inactive
        issuance fail at the authoritative token table.

        Arguments:
            execute: Django database execution callback.
            sql: SQL statement sent to PostgreSQL.
            *arguments: Remaining execution-wrapper arguments.

        Returns:
            Database-driver result for every unrelated statement.

        Raises:
            DatabaseError: If the statement inserts an activation token.
            TypeError: If Django supplies a non-string SQL statement.
        """
        if not isinstance(sql, str):
            message = "database execution supplied non-string SQL"
            raise TypeError(message)
        if 'INSERT INTO "accounts_activationtoken"' in sql:
            raise DatabaseError
        return cast("Callable[..., object]", execute)(sql, *arguments)

    monkeypatch.setattr(send_activation_email, "apply_async", capture)
    with connections["default"].execute_wrapper(fail_token_insert):
        responses = [
            client.post(
                "/api/v1/users/resend_activation/",
                {"email": address},
                content_type="application/json",
                REMOTE_ADDR=f"198.51.100.{index + 60}",
            )
            for index, address in enumerate(
                (
                    f"unknown-write-failure-{suffix}@localforge.invalid",
                    active.email,
                    inactive.email,
                )
            )
        ]

    assert [response.status_code for response in responses] == [
        HTTPStatus.ACCEPTED
    ] * EXPECTED_ACCOUNT_STATE_COUNT
    assert [response.json() for response in responses] == [
        responses[0].json()
    ] * EXPECTED_ACCOUNT_STATE_COUNT
    assert len(published) == EXPECTED_ACCOUNT_STATE_COUNT
    assert all(account_id not in {str(active.pk), str(inactive.pk)} for account_id, _ in published)
    assert not ActivationToken.objects.using("default").exists()
    assert mail.outbox == []


@pytest.mark.integration
@pytest.mark.api_runtime_exempt
@pytest.mark.services("postgres")
def test_activation_routes_document_every_reachable_response() -> None:
    """Expose complete activation and resend contracts in OpenAPI.

    Generates the public schema and verifies the fixed routes, supported POST methods, every
    successful and failure status, examples, and the four stable token-rejection codes.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If a route, method, status, example, or rejection code is missing.
    """
    generator = import_module("drf_spectacular.generators").SchemaGenerator()
    schema = cast("dict[str, Any]", generator.get_schema(request=None, public=True))
    paths = cast("dict[str, Any]", schema["paths"])
    users = cast("dict[str, Any]", paths["/api/v1/users/"])
    resend = cast("dict[str, Any]", paths["/api/v1/users/resend_activation/"])
    user_responses = cast("dict[str, Any]", users["post"]["responses"])
    resend_responses = cast("dict[str, Any]", resend["post"]["responses"])

    assert set(users) == {"post"}
    assert set(resend) == {"post"}
    assert set(user_responses) == {
        "201",
        "204",
        "400",
        "405",
        "406",
        "413",
        "415",
        "429",
        "500",
        "503",
    }
    assert set(resend_responses) == {
        "202",
        "400",
        "405",
        "406",
        "413",
        "415",
        "429",
        "500",
        "503",
    }
    for status, response in {**user_responses, **resend_responses}.items():
        if status == "204":
            continue
        examples = cast(
            "dict[str, Any]",
            cast("dict[str, Any]", response["content"])["application/json"]["examples"],
        )
        assert examples

    bad_request_examples = cast(
        "dict[str, Any]",
        cast("dict[str, Any]", user_responses["400"]["content"])["application/json"]["examples"],
    )
    documented_codes = {
        cast("dict[str, Any]", example["value"])["code"]
        for example in bad_request_examples.values()
    }

    assert {
        ErrorCode.ACTIVATION_TOKEN_EXPIRED,
        ErrorCode.ACTIVATION_TOKEN_FOREIGN,
        ErrorCode.ACTIVATION_TOKEN_MALFORMED,
        ErrorCode.ACTIVATION_TOKEN_USED,
    } <= documented_codes


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_resend_observed_statuses_exactly_match_its_documented_contract(
    client: Client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Compare every reachable resend status with OpenAPI.

    Exercises success, validation, routing, negotiation, size, representation, throttling,
    dependency loss, and unexpected failure through the public versioned endpoint.

    Arguments:
        client: Django test client issuing versioned requests.
        monkeypatch: Fixture inducing documented dependency and unexpected failures.

    Returns:
        None.

    Raises:
        AssertionError: If observed and documented resend statuses differ.
    """
    suffix = uuid.uuid4().hex
    high_rate = "1000/minute"
    with override_settings(
        ACCOUNT_ACTIVATION_RESEND_ADDRESS_THROTTLE_RATE=high_rate,
        ACCOUNT_ACTIVATION_RESEND_ACCOUNT_THROTTLE_RATE=high_rate,
    ):
        success = client.post(
            "/api/v1/users/resend_activation/",
            {"email": f"success-{suffix}@localforge.invalid"},
            content_type="application/json",
            REMOTE_ADDR="198.51.100.31",
        )
        invalid = client.post(
            "/api/v1/users/resend_activation/",
            {},
            content_type="application/json",
            REMOTE_ADDR="198.51.100.32",
        )
        method_not_allowed = client.get("/api/v1/users/resend_activation/")
        not_acceptable = client.post(
            "/api/v1/users/resend_activation/",
            {"email": f"negotiation-{suffix}@localforge.invalid"},
            content_type="application/json",
            headers={"accept": "text/plain"},
            REMOTE_ADDR="198.51.100.33",
        )
        with override_settings(API_REQUEST_BODY_MAX_BYTES=1):
            too_large = client.post(
                "/api/v1/users/resend_activation/",
                data="oversized",
                content_type="text/plain",
                REMOTE_ADDR="198.51.100.34",
            )
        unsupported = client.post(
            "/api/v1/users/resend_activation/",
            data="unsupported",
            content_type="text/plain",
            REMOTE_ADDR="198.51.100.35",
        )

    with override_settings(
        ACCOUNT_ACTIVATION_RESEND_ADDRESS_THROTTLE_RATE="1/minute",
        ACCOUNT_ACTIVATION_RESEND_ACCOUNT_THROTTLE_RATE=high_rate,
    ):
        client.post(
            "/api/v1/users/resend_activation/",
            {"email": f"limit-first-{suffix}@localforge.invalid"},
            content_type="application/json",
            REMOTE_ADDR="198.51.100.36",
        )
        throttled = client.post(
            "/api/v1/users/resend_activation/",
            {"email": f"limit-second-{suffix}@localforge.invalid"},
            content_type="application/json",
            REMOTE_ADDR="198.51.100.36",
        )

    with monkeypatch.context() as failure_patch:
        failure_patch.setattr(
            user_profiles_module,
            "request_activation_resend",
            lambda _email: (_ for _ in ()).throw(RuntimeError("induced")),
        )
        client.raise_request_exception = False
        with override_settings(
            ACCOUNT_ACTIVATION_RESEND_ADDRESS_THROTTLE_RATE=high_rate,
            ACCOUNT_ACTIVATION_RESEND_ACCOUNT_THROTTLE_RATE=high_rate,
        ):
            unexpected = client.post(
                "/api/v1/users/resend_activation/",
                {"email": f"failure-{suffix}@localforge.invalid"},
                content_type="application/json",
                REMOTE_ADDR="198.51.100.37",
            )

    def fail_admission(
        _store: PostgresLoginThrottleStore,
        _rules: object,
        *,
        member: str,
    ) -> None:
        """Raise the authoritative outage documented by resend.

        Replaces only primary admission persistence while the route, middleware, correlation, and
        shared error handler remain real.

        Arguments:
            _store: Primary-backed throttle store receiving the request.
            _rules: Resend rolling-window rules.
            member: Opaque correlated request member.

        Returns:
            Never returns.

        Raises:
            DatabaseError: Always, representing loss of authoritative admission state.
        """
        del member
        raise DatabaseError

    with monkeypatch.context() as outage_patch:
        outage_patch.setattr(PostgresLoginThrottleStore, "admit", fail_admission)
        unavailable = client.post(
            "/api/v1/users/resend_activation/",
            {"email": f"outage-{suffix}@localforge.invalid"},
            content_type="application/json",
            REMOTE_ADDR="198.51.100.38",
        )

    responses = (
        success,
        invalid,
        method_not_allowed,
        not_acceptable,
        too_large,
        unsupported,
        throttled,
        unexpected,
        unavailable,
    )
    observed = {response.status_code for response in responses}
    generator = import_module("drf_spectacular.generators").SchemaGenerator()
    schema = cast("dict[str, Any]", generator.get_schema(request=None, public=True))
    documented = {
        int(status)
        for status in cast(
            "dict[str, Any]",
            cast("dict[str, Any]", schema["paths"])["/api/v1/users/resend_activation/"]["post"][
                "responses"
            ],
        )
    }

    assert observed == documented
    for response in responses[1:]:
        payload = cast("dict[str, Any]", response.json())
        assert payload["request_id"] == response.headers["X-Request-ID"]


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_resend_rejects_missing_invalid_and_undeclared_fields(client: Client) -> None:
    """Keep activation resend input on its one-field contract.

    Submits missing, malformed, and additional values through HTTP so clients receive field-keyed
    validation details rather than having misspelled or privileged input silently ignored.

    Arguments:
        client: Django test client issuing versioned requests.

    Returns:
        None.

    Raises:
        AssertionError: If invalid input is accepted or its detail loses the submitted field.
    """
    responses = [
        client.post(
            "/api/v1/users/resend_activation/",
            body,
            content_type="application/json",
            REMOTE_ADDR=f"198.51.100.{index + 1}",
        )
        for index, body in enumerate(
            (
                {},
                {"email": "not-an-address"},
                {"email": "İ@example.com"},
                {
                    "email": f"valid-{uuid.uuid4().hex}@localforge.invalid",
                    "is_active": True,
                },
            )
        )
    ]
    responses.append(
        client.post(
            "/api/v1/users/resend_activation/",
            data="[]",
            content_type="application/json",
            REMOTE_ADDR="198.51.100.5",
        )
    )
    details = [
        cast("dict[str, Any]", cast("dict[str, Any]", response.json())["details"])
        for response in responses
    ]

    assert [response.status_code for response in responses] == [HTTPStatus.BAD_REQUEST] * 5
    assert [set(detail) for detail in details] == [
        {"email"},
        {"email"},
        {"email"},
        {"is_active"},
        {"non_field_errors"},
    ]


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@pytest.mark.parametrize(
    "failure_type",
    [
        pytest.param(DatabaseError, id="database-error"),
        pytest.param(ServiceUnavailable, id="service-unavailable"),
    ],
)
def test_registration_token_failure_is_identical_for_every_candidate_state(
    client: Client,
    monkeypatch: pytest.MonkeyPatch,
    failure_type: type[Exception],
) -> None:
    """Contain partial activation issuance behind one registration success contract.

    Raises each supported token-creation failure after writing partial token state for new,
    inactive-email, active-email, and username-only candidates, then compares their public success
    responses and verifies rollback leaves only the seeded accounts.

    Arguments:
        client: Django test client issuing versioned requests.
        monkeypatch: Fixture replacing issuance and capturing dummy publication.
        failure_type: Token-creation exception class raised after partial persistence.

    Returns:
        None.

    Raises:
        AssertionError: If candidate state changes the public result or leaves account, token, or
            mail state behind.
    """
    suffix = uuid.uuid4().hex
    inactive = User.objects.create_user(
        f"registration-failure-inactive-{suffix}",
        f"registration-failure-inactive-{suffix}@localforge.invalid",
        PASSWORD,
    )
    active = User.objects.create_user(
        f"registration-failure-active-{suffix}",
        f"registration-failure-active-{suffix}@localforge.invalid",
        PASSWORD,
        is_active=True,
    )
    published: list[tuple[str, str]] = []

    def persist_partially_then_fail(account: User) -> None:
        """Write partial token state before raising the selected issuance failure.

        Exercises registration's savepoint rather than failing before persistence, proving both
        project and database exceptions remove an already-written token row.

        Arguments:
            account: New or duplicate inactive account receiving issuance.

        Returns:
            Never returns.

        Raises:
            Exception: Always, using the parametrized token-creation failure type.
        """
        ActivationToken.objects.using("default").create(
            account=account,
            subject_id=account.pk,
            digest=uuid.uuid4().hex * 2,
        )
        raise failure_type

    def capture(
        *,
        args: tuple[str, str],
        expires: int,
    ) -> None:
        """Capture one state-independent dummy publication.

        Records queue payload identities without executing delivery so every registration candidate
        can be compared without an SMTP side effect.

        Arguments:
            args: Account-shaped key and signed activation token.
            expires: Remaining token lifetime supplied to Celery.

        Returns:
            None.
        """
        del expires
        published.append(args)

    monkeypatch.setattr(user_profiles_module, "issue_activation", persist_partially_then_fail)
    monkeypatch.setattr(send_activation_email, "apply_async", capture)
    requests = (
        _registration_payload(f"registration-failure-new-{suffix}"),
        _registration_payload(f"registration-failure-inactive-replacement-{suffix}")
        | {"email": inactive.email.upper()},
        _registration_payload(f"registration-failure-active-replacement-{suffix}")
        | {"email": active.email.upper()},
        _registration_payload(inactive.username.upper())
        | {"email": f"registration-failure-unrelated-{suffix}@localforge.invalid"},
    )

    responses = [
        client.post(
            "/api/v1/users/",
            body,
            content_type="application/json",
            REMOTE_ADDR=f"198.51.100.{index + 70}",
        )
        for index, body in enumerate(requests)
    ]

    assert [response.status_code for response in responses] == [
        HTTPStatus.CREATED
    ] * EXPECTED_REGISTRATION_CANDIDATE_COUNT
    assert [response.json() for response in responses] == [
        {"username": body["username"], "email": body["email"].lower()} for body in requests
    ]
    assert len(published) == EXPECTED_REGISTRATION_CANDIDATE_COUNT
    assert all(
        account_id not in {str(inactive.pk), str(active.pk)} for account_id, _token in published
    )
    assert set(User.objects.using("default").values_list("pk", flat=True)) == {
        inactive.pk,
        active.pk,
    }
    assert not ActivationToken.objects.using("default").exists()
    assert mail.outbox == []


@pytest.mark.integration
@pytest.mark.services("postgres", "rabbitmq", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_task_publication_failure_does_not_change_registration_response(
    client: Client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Contain a broker failure after account commit.

    Raises at the task-publication boundary and verifies the already committed public registration
    remains accepted without falling back to synchronous SMTP delivery.

    Arguments:
        client: Django test client issuing the versioned request.
        monkeypatch: Fixture making the queue publication fail.

    Returns:
        None.

    Raises:
        AssertionError: If the broker failure escapes or sends email synchronously.
    """

    def fail_publication(
        *,
        args: tuple[str, str],
        expires: int,
    ) -> None:
        """Raise one broker publication failure.

        Represents an unavailable queue without exposing connection details or changing any
        database behavior.

        Arguments:
            args: Account key and signed token the task would receive.
            expires: Remaining token lifetime the broker would enforce.

        Returns:
            Never returns.

        Raises:
            OperationalError: Always, representing unavailable broker transport.
        """
        del args, expires
        raise OperationalError

    monkeypatch.setattr(send_activation_email, "apply_async", fail_publication)
    username = f"publish-outage-{uuid.uuid4().hex}"

    response = client.post(
        "/api/v1/users/",
        _registration_payload(username),
        content_type="application/json",
    )

    assert response.status_code == HTTPStatus.CREATED
    assert User.objects.using("default").filter(username=username, is_active=False).exists()
    assert mail.outbox == []


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_validly_signed_unissued_and_invalid_payload_tokens_are_foreign(
    client: Client,
) -> None:
    """Reject signed values that no activation issuance produced.

    Signs one payload with an invalid nonce type and one valid-shaped payload lacking a digest row,
    then submits both through HTTP to exercise foreign-token handling without account disclosure.

    Arguments:
        client: Django test client issuing versioned requests.

    Returns:
        None.

    Raises:
        AssertionError: If either signed but unissued value receives another classification.
    """
    account = User.objects.create_user(
        f"signed-foreign-{uuid.uuid4().hex}",
        f"signed-foreign-{uuid.uuid4().hex}@localforge.invalid",
        PASSWORD,
    )
    unknown_account = str(uuid.uuid4())
    cases = (
        (
            str(account.pk),
            signing.dumps(
                {"account": str(account.pk), "nonce": 7},
                salt=ACTIVATION_SIGNING_SALT,
            ),
        ),
        (
            str(account.pk),
            signing.dumps(
                {"account": str(account.pk), "nonce": "not-issued"},
                salt=ACTIVATION_SIGNING_SALT,
            ),
        ),
        (
            unknown_account,
            signing.dumps(
                {"account": unknown_account, "nonce": "unknown-account"},
                salt=ACTIVATION_SIGNING_SALT,
            ),
        ),
    )

    responses = [
        client.post(
            "/api/v1/users/",
            {"account": account_id, "token": token},
            content_type="application/json",
        )
        for account_id, token in cases
    ]

    assert [response.status_code for response in responses] == [HTTPStatus.BAD_REQUEST] * 3
    assert [cast("dict[str, Any]", response.json())["code"] for response in responses] == [
        ErrorCode.ACTIVATION_TOKEN_FOREIGN
    ] * 3


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_activation_persistence_failure_is_service_unavailable(client: Client) -> None:
    """Fail closed when activation cannot lock authoritative account state.

    Registers normally, then rejects the confirmation's locking account query at the database
    boundary while preserving token parsing and the public error envelope.

    Arguments:
        client: Django test client issuing versioned requests.

    Returns:
        None.

    Raises:
        AssertionError: If activation succeeds or the database outage is misclassified.
    """
    fields = _register_for_activation(client, f"activation-outage-{uuid.uuid4().hex}")

    def fail_account_lock(execute: object, sql: object, *arguments: object) -> object:
        """Reject only the account row lock used by confirmation.

        Preserves all other database operations so the test reaches the activation transaction
        before simulating authoritative state loss.

        Arguments:
            execute: Django database execution callback.
            sql: SQL statement sent to PostgreSQL.
            *arguments: Remaining execution-wrapper arguments.

        Returns:
            Database driver's result for unrelated statements.

        Raises:
            DatabaseError: If activation requests the account row lock.
            TypeError: If Django supplies a non-string SQL statement.
        """
        if not isinstance(sql, str):
            message = "database execution supplied non-string SQL"
            raise TypeError(message)
        if 'FROM "accounts_user"' in sql and "FOR UPDATE" in sql:
            raise DatabaseError
        return cast("Callable[..., object]", execute)(sql, *arguments)

    with connections["default"].execute_wrapper(fail_account_lock):
        response = client.post(
            "/api/v1/users/",
            fields,
            content_type="application/json",
        )

    assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert cast("dict[str, Any]", response.json())["code"] == ErrorCode.SERVICE_UNAVAILABLE
    assert User.objects.using("default").get(pk=fields["account"]).is_active is False


@pytest.mark.integration
@pytest.mark.services("postgres", "valkey-cache")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_resend_account_lookup_failure_after_admission_is_service_unavailable(
    client: Client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail closed when resend loses account state after admission.

    Allows the throttle's identity lookup and fails the subsequent transactional lookup, proving
    accepted admission does not make account persistence optional.

    Arguments:
        client: Django test client issuing the versioned request.
        monkeypatch: Fixture controlling the two authoritative lookup calls.

    Returns:
        None.

    Raises:
        AssertionError: If the second outage is accepted or loses its stable code.
    """
    calls = 0

    def resolve_then_fail(_email: str) -> User | None:
        """Resolve the throttle identity and fail the transactional lookup.

        Returns no account for the first call and raises on the second, matching the endpoint's two
        primary-backed phases.

        Arguments:
            _email: Normalized submitted address.

        Returns:
            None on the admission lookup.

        Raises:
            DatabaseError: On the transactional resend lookup.
        """
        nonlocal calls
        calls += 1
        if calls == 1:
            return None
        raise DatabaseError

    monkeypatch.setattr(
        "accounts.account_activation.resolve_activation_account",
        resolve_then_fail,
    )

    response = client.post(
        "/api/v1/users/resend_activation/",
        {"email": f"second-outage-{uuid.uuid4().hex}@localforge.invalid"},
        content_type="application/json",
    )

    assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert cast("dict[str, Any]", response.json())["code"] == ErrorCode.SERVICE_UNAVAILABLE
    assert calls == EXPECTED_ACCOUNT_COUNT


@pytest.mark.integration
@pytest.mark.services("postgres", "mailpit", "valkey-cache")
@pytest.mark.serial
@pytest.mark.timeout(MAILPIT_TIMEOUT_SECONDS)
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@pytest.mark.skipif(
    settings.EMAIL_BACKEND != SMTP_BACKEND,
    reason="the real activation loop runs only under the Compose smtp profile",
)
@override_settings(EMAIL_BACKEND=SMTP_BACKEND)
def test_registration_activation_loop_round_trips_through_mailpit(client: Client) -> None:
    """Register, capture, and activate through real SMTP.

    Clears the shared capture service, registers through HTTP, polls Mailpit for the multipart
    message, then posts its link values back to the versioned account route. This test is serial
    because clearing one profile-gated Mailpit instance cannot be namespaced per worker.

    Arguments:
        client: Django test client issuing versioned requests.

    Returns:
        None.

    Raises:
        AssertionError: If SMTP capture, multipart content, secrecy, or activation fails.
    """
    _mailpit_request("DELETE", "/api/v1/messages")
    username = f"smtp-activation-{uuid.uuid4().hex}"
    email = f"{username}@localforge.invalid"

    registered = client.post(
        "/api/v1/users/",
        _registration_payload(username),
        content_type="application/json",
    )
    assert registered.status_code == HTTPStatus.CREATED

    deadline = time.monotonic() + MAILPIT_TIMEOUT_SECONDS
    detail: MailpitMessageDetail | None = None
    while time.monotonic() < deadline:
        listing = cast("MailpitMessages", _mailpit_request("GET", "/api/v1/messages"))
        if listing["messages"]:
            captured = listing["messages"][0]
            assert captured["Subject"] == "Activate your LocalForge account"
            assert [address["Address"] for address in captured["To"]] == [email]
            detail = cast(
                "MailpitMessageDetail",
                _mailpit_request("GET", f"/api/v1/message/{captured['ID']}"),
            )
            break
        time.sleep(MAILPIT_POLL_SECONDS)

    assert detail is not None
    assert detail["Text"].strip()
    assert detail["HTML"].strip()
    assert PASSWORD not in detail["Text"]
    assert PASSWORD not in detail["HTML"]
    assert settings.SECRET_KEY not in detail["Text"]
    assert settings.JWT_SIGNING_KEY not in detail["Text"]

    links = [word for word in detail["Text"].split() if word.startswith("http")]
    assert len(links) == 1
    parsed = parse_qs(urlparse(links[0]).query)
    assert links[0].startswith(settings.SITE_URL)
    assert parsed["account"][0] in detail["HTML"]
    assert quote(parsed["token"][0], safe="") in detail["HTML"]
    activated = client.post(
        "/api/v1/users/",
        {"account": parsed["account"][0], "token": parsed["token"][0]},
        content_type="application/json",
    )

    assert activated.status_code == HTTPStatus.NO_CONTENT
    assert User.objects.using("default").get(username=username).is_active is True
