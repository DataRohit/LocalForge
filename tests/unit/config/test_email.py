"""Unit tests for application email delivery.

Covers absolute site links, multipart rendering, backend refusal, and expected SMTP failures
without requiring a mail service from the isolated unit layer.
"""

import logging
import smtplib
from typing import TYPE_CHECKING, cast
from unittest import mock

import pytest
from django.core import mail
from django.test import override_settings

from config.email import build_site_url, send_application_email
from config.logs import StructuredFormatter

if TYPE_CHECKING:
    from typing import Protocol

    from django.core.mail import EmailMultiAlternatives

    class EmailFailureRecord(Protocol):
        """Failure-record fields added by the application.

        Describes the safe error classification attached to the log record, so the assertion can
        inspect the extra field without weakening typing. Inherits from Protocol.

        Attributes:
            email_error_type: Exception class recorded without its sensitive message.

        Members:
            None. The protocol carries one record field only.
        """

        email_error_type: str


RECIPIENT = "recipient@localforge.invalid"
SENDER = "sender@localforge.invalid"
SUBJECT = "Account ready"
MESSAGE = "Your account is ready & waiting <now>."


@pytest.mark.unit
@override_settings(SITE_URL="http://localforge.localhost:8080")
def test_site_paths_become_absolute_urls() -> None:
    """Join an application path onto the environment's site URL.

    Confirms leading and trailing separators cannot discard the configured host, which keeps links
    correct when a caller passes an ordinary route beginning with a slash.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the path is not placed beneath the configured site.
    """
    assert build_site_url("/users/me/") == "http://localforge.localhost:8080/users/me/"


@pytest.mark.unit
@override_settings(
    DEFAULT_FROM_EMAIL=SENDER,
    DEFAULT_REPLY_TO_EMAIL="support@localforge.invalid",
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    SITE_NAME="LocalForge Test",
    SITE_URL="http://localhost:8000",
)
def test_application_email_contains_text_html_and_site_identity() -> None:
    """Render and accept a multipart application email.

    Confirms the sender and site identity come from settings, the plain part is non-empty, the HTML
    alternative exists, and the action path becomes an absolute environment-specific URL.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If rendering or delivery omits any required message part.
    """
    delivered = send_application_email(
        RECIPIENT,
        SUBJECT,
        MESSAGE,
        action_path="/users/me/?tab=profile&source=email",
        context={"unused_probe": "available"},
    )

    assert delivered is True
    assert len(mail.outbox) == 1

    message = cast("EmailMultiAlternatives", mail.outbox[0])
    assert message.from_email == SENDER
    assert message.reply_to == ["support@localforge.invalid"]
    assert message.to == [RECIPIENT]
    assert message.subject == SUBJECT
    assert message.body.strip()
    assert "LocalForge Test" in message.body
    assert MESSAGE in message.body
    assert "http://localhost:8000/users/me/?tab=profile&source=email" in message.body
    assert len(message.alternatives) == 1
    alternative_content, alternative_type = message.alternatives[0]
    rendered_html = str(alternative_content)
    assert alternative_type == "text/html"
    assert "LocalForge Test" in rendered_html
    assert "Your account is ready &amp; waiting &lt;now&gt;." in rendered_html
    assert "http://localhost:8000/users/me/?tab=profile&amp;source=email" in rendered_html


@pytest.mark.unit
@override_settings(
    DEFAULT_FROM_EMAIL=SENDER,
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    SITE_NAME="LocalForge Test",
    SITE_URL="http://localhost:8000",
)
def test_a_backend_refusal_returns_false() -> None:
    """Report when a backend accepts no message.

    Confirms a zero delivery count is not success and exercises a message without an action URL or
    additional context, which is valid for informational mail.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If a refused message is reported as delivered.
    """
    with (
        mock.patch("django.core.mail.EmailMultiAlternatives.send", return_value=0),
        mock.patch("config.email.logger.error") as log_error,
    ):
        delivered = send_application_email(RECIPIENT, SUBJECT, MESSAGE)

    assert delivered is False
    log_error.assert_called_once()


@pytest.mark.unit
@override_settings(
    DEFAULT_FROM_EMAIL=SENDER,
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    SITE_NAME="LocalForge Test",
    SITE_URL="http://localhost:8000",
)
def test_an_smtp_failure_is_logged_without_escaping(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Contain and record an expected transport failure.

    Confirms an unavailable relay produces a structured failure record and a false result rather
    than raising into the request that attempted to send the message.

    Arguments:
        caplog: Fixture collecting records emitted during the failed send.

    Returns:
        None.

    Raises:
        AssertionError: If the failure escapes, is unlogged, or exposes the recipient.
    """
    failure = smtplib.SMTPRecipientsRefused(
        {RECIPIENT: (550, b"sensitive server response")},
    )

    with (
        caplog.at_level(logging.ERROR, logger="config.email"),
        mock.patch("django.core.mail.EmailMultiAlternatives.send", side_effect=failure),
    ):
        delivered = send_application_email(RECIPIENT, SUBJECT, MESSAGE)

    record = caplog.records[-1]
    failure_record = cast("EmailFailureRecord", record)
    rendered = StructuredFormatter().format(record)
    assert delivered is False
    assert "Email delivery failed" in rendered
    assert record.exc_info is None
    assert failure_record.email_error_type == "SMTPRecipientsRefused"
    assert RECIPIENT not in rendered
    assert SUBJECT not in rendered
    assert "sensitive server response" not in rendered


@pytest.mark.unit
@override_settings(
    DEFAULT_FROM_EMAIL=SENDER,
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    SITE_NAME="LocalForge Test",
    SITE_URL="http://localhost:8000",
)
def test_an_invalid_header_is_logged_without_escaping(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Contain a message serialization failure.

    Confirms a malformed header is treated like another delivery failure and logged only by error
    class, so caller-controlled subject text cannot escape into the response or structured logs.

    Arguments:
        caplog: Fixture collecting records emitted during the failed send.

    Returns:
        None.

    Raises:
        AssertionError: If the failure escapes or its subject reaches the log.
    """
    unsafe_subject = "unsafe\nsubject"

    with caplog.at_level(logging.ERROR, logger="config.email"):
        delivered = send_application_email(RECIPIENT, unsafe_subject, MESSAGE)

    record = caplog.records[-1]
    failure_record = cast("EmailFailureRecord", record)
    rendered = StructuredFormatter().format(record)
    assert delivered is False
    assert failure_record.email_error_type == "ValueError"
    assert unsafe_subject not in rendered
