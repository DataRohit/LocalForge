"""Runtime integration probes requiring host-controlled service transitions.

Runs the same assertions from host and container settings while the operator command recreates
Mailpit or stops a real dependency outside the test process.
"""

from __future__ import annotations

import argparse
import http.client
import json
import smtplib
from email.message import EmailMessage
from http import HTTPStatus
from typing import TypedDict, cast

import django
from django.conf import settings
from django.test import Client

MAILPIT_TIMEOUT_SECONDS = 15
HOST_RECIPIENT = "runtime-host@localforge.invalid"
CONTAINER_RECIPIENT = "runtime-container@localforge.invalid"
RUNTIME_SUBJECT = "LocalForge Mailpit recreation audit"
EXPECTED_RECIPIENTS = frozenset({HOST_RECIPIENT, CONTAINER_RECIPIENT})
EXPECTED_HEALTH_CHECKS = (
    "broker",
    "cache",
    "channel_layer",
    "database_primary",
    "database_replica",
    "mail",
    "object_storage",
)


class MailpitAddress(TypedDict):
    """Address returned by the Mailpit API.

    Inherits from ``TypedDict`` and carries the mailbox value used by persistence assertions.
    Keeps the response boundary explicit without exposing fields the probe does not consume.

    Attributes:
        Address: Captured mailbox address.

    Members:
        None.
    """

    Address: str


class MailpitMessage(TypedDict):
    """Message summary returned by the Mailpit API.

    Inherits from ``TypedDict`` and carries the subject and recipients used by the audit.
    Keeps persistence checks typed while ignoring unrelated presentation fields.

    Attributes:
        Subject: Captured message subject.
        To: Captured recipient addresses.

    Members:
        None.
    """

    Subject: str
    To: list[MailpitAddress]


class MailpitMessages(TypedDict):
    """Message listing returned by the Mailpit API.

    Inherits from ``TypedDict`` and exposes the summaries inspected by the audit.
    Keeps the decoded response shape narrow and stable for both execution modes.

    Attributes:
        messages: Captured message summaries.

    Members:
        None.
    """

    messages: list[MailpitMessage]


def mailpit_request(method: str) -> MailpitMessages | None:
    """Call the configured Mailpit messages endpoint.

    Uses environment-specific settings so the same process reaches the service by container name
    or published loopback port without branching on its execution location.

    Arguments:
        method: HTTP method sent to the messages endpoint.

    Returns:
        Parsed listing for GET, otherwise None.

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

    assert response.status == HTTPStatus.OK
    if method != "GET":
        return None

    return cast("MailpitMessages", json.loads(payload))


def seed_mailpit(mode: str) -> None:
    """Send one mode-identifying message through the configured SMTP endpoint.

    Uses a fixed non-sensitive recipient for each execution location so later assertions can prove
    both modes reached the same persistent capture store.

    Arguments:
        mode: ``host`` or ``container``.

    Returns:
        None.

    Raises:
        ValueError: If the mode is unsupported.
    """
    recipients = {
        "host": HOST_RECIPIENT,
        "container": CONTAINER_RECIPIENT,
    }
    try:
        recipient = recipients[mode]
    except KeyError as error:
        msg = f"unsupported runtime probe mode: {mode}"
        raise ValueError(msg) from error

    message = EmailMessage()
    message["From"] = settings.DEFAULT_FROM_EMAIL
    message["To"] = recipient
    message["Subject"] = RUNTIME_SUBJECT
    message.set_content("LocalForge runtime integration audit")

    with smtplib.SMTP(
        settings.EMAIL_HOST,
        settings.EMAIL_PORT,
        timeout=MAILPIT_TIMEOUT_SECONDS,
    ) as client:
        client.send_message(message)


def verify_mailpit() -> None:
    """Require both mode-identifying messages in the shared capture store.

    Reads through the current execution mode and selects only the fixed audit subject, proving the
    host and container messages survive without depending on unrelated captured mail.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If either message is absent or duplicated.
    """
    listing = mailpit_request("GET")
    assert listing is not None
    recipients = {
        address["Address"]
        for message in listing["messages"]
        if message["Subject"] == RUNTIME_SUBJECT
        for address in message["To"]
    }

    assert recipients == EXPECTED_RECIPIENTS


def verify_mailpit_empty() -> None:
    """Require Mailpit to contain no captured messages.

    Reads the store after the operator command deletes all messages, proving cleanup is visible
    from either execution mode rather than assuming the DELETE response was sufficient.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If any captured message remains.
    """
    listing = mailpit_request("GET")
    assert listing is not None
    assert listing["messages"] == []


def verify_health(expected: str) -> None:
    """Observe readiness while the real cache is running or stopped.

    Calls the public health route through Django's middleware stack and requires the exact cache
    state and response status expected from the host-controlled service transition.

    Arguments:
        expected: ``ready`` or ``degraded``.

    Returns:
        None.

    Raises:
        AssertionError: If readiness does not match the real dependency state.
        ValueError: If the expected state is unsupported.
    """
    expectations: dict[str, tuple[HTTPStatus, str, dict[str, str]]] = {
        "ready": (
            HTTPStatus.OK,
            "ready",
            dict.fromkeys(EXPECTED_HEALTH_CHECKS, "working"),
        ),
        "degraded": (
            HTTPStatus.SERVICE_UNAVAILABLE,
            "not_ready",
            {
                **dict.fromkeys(EXPECTED_HEALTH_CHECKS, "working"),
                "cache": "unavailable",
            },
        ),
    }
    try:
        status, readiness, checks = expectations[expected]
    except KeyError as error:
        msg = f"unsupported health expectation: {expected}"
        raise ValueError(msg) from error

    response = Client().get(
        "/health/",
        headers={"accept": "application/json"},
        HTTP_HOST="localhost",
    )
    payload = cast("dict[str, object]", response.json())

    assert response.status_code == status
    assert payload == {
        "status": readiness,
        "liveness": "alive",
        "readiness": readiness,
        "checks": checks,
    }


def build_parser() -> argparse.ArgumentParser:
    """Build the runtime probe command parser.

    Restricts direct invocation to the exact operations sequenced by the supported operator
    command, keeping destructive service transitions outside this process.

    Arguments:
        None.

    Returns:
        Configured parser.
    """
    parser = argparse.ArgumentParser(description="Run one LocalForge runtime integration probe.")
    parser.add_argument(
        "action",
        choices=(
            "mailpit-clear",
            "mailpit-empty",
            "mailpit-seed",
            "mailpit-verify",
            "health-degraded",
            "health-ready",
        ),
    )
    parser.add_argument("mode", nargs="?")

    return parser


def main(argv: list[str] | None = None) -> int:
    """Run one runtime integration probe.

    Initializes Django once, dispatches the requested assertion, and returns only after the live
    service behavior has been observed successfully.

    Arguments:
        argv: Command arguments, or None to read process arguments.

    Returns:
        Zero after a successful probe.

    Raises:
        AssertionError: If live behavior violates the required contract.
        ValueError: If a required mode is missing or invalid.
    """
    arguments = build_parser().parse_args(argv)
    django.setup()

    if arguments.action == "mailpit-clear":
        mailpit_request("DELETE")
    elif arguments.action == "mailpit-empty":
        verify_mailpit_empty()
    elif arguments.action == "mailpit-seed":
        if arguments.mode is None:
            msg = "mailpit-seed requires a mode"
            raise ValueError(msg)
        seed_mailpit(arguments.mode)
    elif arguments.action == "mailpit-verify":
        verify_mailpit()
    elif arguments.action == "health-degraded":
        verify_health("degraded")
    else:
        verify_health("ready")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
