"""Integration tests for SMTP email capture.

Runs only when the testing environment is explicitly switched from locmem to SMTP under the
Compose smtp profile, then asserts on Mailpit's API rather than Django's in-process outbox.
"""

import http.client
import json
import time
from typing import TypedDict, cast

import pytest
from django.conf import settings
from django.test import override_settings

from config.email import send_application_email

SMTP_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
MAILPIT_TIMEOUT_SECONDS = 15
POLL_INTERVAL_SECONDS = 0.1
RECIPIENT = "smtp-round-trip@localforge.invalid"
SUBJECT = "SMTP round trip"


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
        connection.request(method, "/api/v1/messages", headers={"Host": "localhost"})
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
@pytest.mark.serial
@pytest.mark.timeout(MAILPIT_TIMEOUT_SECONDS)
@pytest.mark.skipif(
    settings.EMAIL_BACKEND != SMTP_BACKEND,
    reason="the real SMTP round trip runs only under the Compose smtp profile",
)
@override_settings(EMAIL_BACKEND=SMTP_BACKEND)
def test_a_message_round_trips_through_smtp_and_mailpit() -> None:
    """Send through SMTP and assert on the capture service.

    Clears Mailpit before sending because its database persists across runs, then polls its API for
    the expected recipient and subject instead of consulting Django's in-process outbox. This test
    is serial because clearing a shared capture service cannot be namespaced per worker.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If delivery fails or Mailpit never reports the expected message.
    """
    _mailpit_request("DELETE")

    assert send_application_email(RECIPIENT, SUBJECT, "Captured by Mailpit.") is True

    deadline = time.monotonic() + MAILPIT_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        listing = _mailpit_request("GET")
        assert listing is not None
        if listing["messages"]:
            captured = listing["messages"][0]
            assert captured["Subject"] == SUBJECT
            assert [address["Address"] for address in captured["To"]] == [RECIPIENT]
            return
        time.sleep(POLL_INTERVAL_SECONDS)

    pytest.fail("Mailpit did not report the captured message before the timeout")
