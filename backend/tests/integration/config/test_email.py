"""Integration tests for SMTP email capture.

Runs only when the testing environment is explicitly switched from locmem to SMTP under the
Compose smtp profile, then asserts on Mailpit's API rather than Django's in-process outbox.
"""

import http.client
import json
import secrets
import time
import uuid
from base64 import b64encode
from typing import TypedDict, cast

import pytest
from django.conf import settings
from django.test import override_settings

from accounts.models import User
from accounts.tasks import send_password_changed_email

SMTP_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
MAILPIT_TIMEOUT_SECONDS = 15
POLL_INTERVAL_SECONDS = 0.1
SUBJECT = "Your LocalForge password changed"


class MailpitAddress(TypedDict):
    """Address shape returned by the Mailpit API.

    Describes the fields the assertion consumes from a captured message recipient. Inherits from
    TypedDict.

    Attributes:
        Address: Mailbox address captured by Mailpit.

    Members:
        None. The type carries response data only.
    """

    Address: str


class MailpitMessage(TypedDict):
    """Message summary returned by the Mailpit API.

    Describes the subject and recipient fields the SMTP integration gate verifies. Inherits from
    TypedDict.

    Attributes:
        Subject: Captured message subject.
        To: Captured recipient addresses.

    Members:
        None. The type carries response data only.
    """

    Subject: str
    To: list[MailpitAddress]


class MailpitMessages(TypedDict):
    """Message listing returned by the Mailpit API.

    Describes the captured summaries used to find the message sent by this test. Inherits from
    TypedDict.

    Attributes:
        messages: Captured message summaries.

    Members:
        None. The type carries response data only.
    """

    messages: list[MailpitMessage]


def _mailpit_request(method: str) -> MailpitMessages | None:
    """Call the testing Mailpit messages endpoint.

    Uses the SMTP host and registered web port from settings, so the same test reaches the internal
    service name in a container and the published loopback port on the host.

    Arguments:
        method: HTTP method to send to the messages endpoint.

    Returns:
        Parsed message data for GET, otherwise None.

    Raises:
        AssertionError: If Mailpit rejects the request.
    """
    connection = http.client.HTTPConnection(
        settings.EMAIL_HOST,
        settings.MAILPIT_WEB_PORT,
        timeout=MAILPIT_TIMEOUT_SECONDS,
    )

    try:
        token = b64encode(settings.MAILPIT_UI_AUTH.encode()).decode()
        connection.request(
            method,
            "/api/v1/messages",
            headers={"Authorization": f"Basic {token}", "Host": "localhost"},
        )
        response = connection.getresponse()
        payload = response.read()
    finally:
        connection.close()

    assert response.status == http.client.OK
    if method != "GET":
        return None

    return cast("MailpitMessages", json.loads(payload))


@pytest.mark.integration
@pytest.mark.services("mailpit")
@pytest.mark.timeout(MAILPIT_TIMEOUT_SECONDS)
def test_mailpit_rejects_unauthenticated_api_access() -> None:
    """Reject access to captured messages without the generated UI credential.

    Calls the API without authorization and verifies metadata is unavailable even from loopback,
    preventing captured account recovery material from becoming an unauthenticated local surface.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If Mailpit exposes message metadata without authentication.
    """
    connection = http.client.HTTPConnection(
        settings.EMAIL_HOST,
        settings.MAILPIT_WEB_PORT,
        timeout=MAILPIT_TIMEOUT_SECONDS,
    )
    try:
        connection.request("GET", "/api/v1/messages", headers={"Host": "localhost"})
        response = connection.getresponse()
        payload = response.read()
    finally:
        connection.close()

    assert response.status == http.client.UNAUTHORIZED
    assert b'"messages"' not in payload.lower()


@pytest.mark.integration
@pytest.mark.services("postgres", "mailpit")
@pytest.mark.serial
@pytest.mark.timeout(MAILPIT_TIMEOUT_SECONDS)
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@override_settings(EMAIL_BACKEND=SMTP_BACKEND)
def test_a_message_round_trips_through_smtp_and_mailpit() -> None:
    """Execute an account task through SMTP and assert on the capture service.

    Clears Mailpit, creates one account, executes the credential-free notification task, then polls
    the API instead of consulting Django's outbox. This test is serial because Mailpit cleanup
    cannot be namespaced per worker.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If delivery fails or Mailpit never reports the expected message.
    """
    _mailpit_request("DELETE")
    suffix = uuid.uuid4().hex
    recipient = f"smtp-round-trip-{suffix}@localforge.invalid"
    account = User.objects.db_manager("default").create_user(
        f"smtp-round-trip-{suffix}",
        recipient,
        secrets.token_urlsafe(24),
        is_active=True,
    )

    assert send_password_changed_email(str(account.pk)) is True

    deadline = time.monotonic() + MAILPIT_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        listing = _mailpit_request("GET")
        assert listing is not None
        if listing["messages"]:
            captured = listing["messages"][0]
            assert captured["Subject"] == SUBJECT
            assert [address["Address"] for address in captured["To"]] == [recipient]
            return
        time.sleep(POLL_INTERVAL_SECONDS)

    pytest.fail("Mailpit did not report the captured message before the timeout")
