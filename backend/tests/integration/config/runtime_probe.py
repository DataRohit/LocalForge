"""Runtime integration probes requiring host-controlled service transitions.

Runs the same assertions from host and container settings while the operator command recreates
Mailpit or stops a real dependency outside the test process.
"""

from __future__ import annotations

import argparse
import http.client
import json
import smtplib
import time
from base64 import b64encode
from email.message import EmailMessage
from http import HTTPStatus
from typing import Any, TypedDict, cast

import django
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.mail import send_mail
from django.test import Client
from kombu import Connection, Exchange, Producer, Queue

MAILPIT_TIMEOUT_SECONDS = 15
HOST_RECIPIENT = "runtime-host@localforge.invalid"
CONTAINER_RECIPIENT = "runtime-container@localforge.invalid"
RUNTIME_SUBJECT = "LocalForge Mailpit recreation audit"
EXPECTED_RECIPIENTS = frozenset({HOST_RECIPIENT, CONTAINER_RECIPIENT})
RESTART_AUDIT_USERNAME = "phase7-restart-audit"
RESTART_AUDIT_EMAIL = "phase7-restart-audit@localforge.invalid"
RESTART_AUDIT_CACHE_KEY = "phase7:restart:audit"
RESTART_AUDIT_VALUE = b"phase7-restart-persistence"
RESTART_AUDIT_OBJECT = "phase7-restart-audit/persistence.bin"
RESTART_AUDIT_QUEUE = "phase7.restart.audit"
RESTART_AUDIT_SUBJECT = "LocalForge restart persistence audit"
RESTART_REPLICA_TIMEOUT_SECONDS = 30
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


def seed_restart_persistence() -> None:
    """Seed durable state in every persistent application backing service.

    Writes one account, cache value, private object, captured email, and quorum-queue message before
    the operator stops the complete stack.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If any backing service refuses its seed value.
    """
    user_model = get_user_model()
    user_model.objects.using("default").filter(username=RESTART_AUDIT_USERNAME).delete()
    account = user_model.objects.db_manager("default").create(
        username=RESTART_AUDIT_USERNAME,
        email=RESTART_AUDIT_EMAIL,
        is_active=True,
    )
    account.set_unusable_password()
    account.save(using="default", update_fields=["password"])
    cache.set(RESTART_AUDIT_CACHE_KEY, RESTART_AUDIT_VALUE, timeout=None)
    assert cache.get(RESTART_AUDIT_CACHE_KEY) == RESTART_AUDIT_VALUE
    default_storage.delete(RESTART_AUDIT_OBJECT)
    assert (
        default_storage.save(RESTART_AUDIT_OBJECT, ContentFile(RESTART_AUDIT_VALUE))
        == RESTART_AUDIT_OBJECT
    )
    assert (
        send_mail(
            RESTART_AUDIT_SUBJECT,
            RESTART_AUDIT_VALUE.decode(),
            settings.DEFAULT_FROM_EMAIL,
            [RESTART_AUDIT_EMAIL],
        )
        == 1
    )
    exchange = Exchange(RESTART_AUDIT_QUEUE, type="direct", durable=True)
    queue = Queue(
        RESTART_AUDIT_QUEUE,
        exchange,
        routing_key=RESTART_AUDIT_QUEUE,
        durable=True,
        queue_arguments={"x-queue-type": "quorum"},
    )
    with Connection(settings.CELERY_BROKER_URL) as connection:
        channel = connection.channel()
        bound = queue(channel)
        bound.delete(if_unused=False, if_empty=False)
        bound.declare()
        Producer(channel, exchange=exchange).publish(
            {"value": RESTART_AUDIT_VALUE.decode()},
            routing_key=RESTART_AUDIT_QUEUE,
            serializer="json",
            declare=[bound],
        )


def verify_restart_persistence() -> None:
    """Verify and remove durable state after a complete stack restart.

    Requires the primary and replica account row, cache bytes, private object, captured message,
    and quorum-queue payload to survive before deleting each audit artifact.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If any persistent service loses its seed value.
    """
    user_model = get_user_model()
    account = user_model.objects.using("default").get(username=RESTART_AUDIT_USERNAME)
    deadline = time.monotonic() + RESTART_REPLICA_TIMEOUT_SECONDS
    replica_exists = False
    while time.monotonic() < deadline:
        replica_exists = user_model.objects.using("replica").filter(pk=account.pk).exists()
        if replica_exists:
            break
        time.sleep(0.25)
    assert replica_exists
    assert cache.get(RESTART_AUDIT_CACHE_KEY) == RESTART_AUDIT_VALUE
    with default_storage.open(RESTART_AUDIT_OBJECT, "rb") as stored:
        assert stored.read() == RESTART_AUDIT_VALUE
    listing = mailpit_request("GET")
    assert listing is not None
    assert any(message["Subject"] == RESTART_AUDIT_SUBJECT for message in listing["messages"])

    exchange = Exchange(RESTART_AUDIT_QUEUE, type="direct", durable=True)
    queue = Queue(
        RESTART_AUDIT_QUEUE,
        exchange,
        routing_key=RESTART_AUDIT_QUEUE,
        durable=True,
        queue_arguments={"x-queue-type": "quorum"},
    )
    with Connection(settings.CELERY_BROKER_URL) as connection:
        channel = connection.channel()
        bound = queue(channel)
        message = bound.get(no_ack=True)
        assert message is not None
        assert cast("dict[str, Any]", message.payload) == {"value": RESTART_AUDIT_VALUE.decode()}
        bound.delete(if_unused=False, if_empty=False)

    user_model.objects.using("default").filter(pk=account.pk).delete()
    cache.delete(RESTART_AUDIT_CACHE_KEY)
    default_storage.delete(RESTART_AUDIT_OBJECT)
    mailpit_request("DELETE")


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
            "restart-seed",
            "restart-verify",
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
    elif arguments.action == "restart-seed":
        seed_restart_persistence()
    elif arguments.action == "restart-verify":
        verify_restart_persistence()
    elif arguments.action == "health-degraded":
        verify_health("degraded")
    else:
        verify_health("ready")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
