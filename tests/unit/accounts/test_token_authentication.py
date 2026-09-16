"""Unit tests for token authentication boundary components.

Covers the permanently inert JSON web token placeholder, security scheme documents, and credential
redaction without touching a database, cache, broker, or any other external service.
"""

import json
import logging
import secrets
from ipaddress import ip_network

import pytest
from django.conf import settings
from django.test import RequestFactory, override_settings
from rest_framework.request import Request

from accounts.authentication import (
    JWTAuthentication,
    JWTAuthenticationScheme,
    PrimaryTokenAuthenticationScheme,
)
from accounts.token_authentication import trusted_client_address
from config.celery import redact_published_arguments
from config.logs import StructuredFormatter


@pytest.mark.unit
def test_pending_jwt_authentication_never_authenticates() -> None:
    """Keep Ticket 29's primary authentication slot permanently inert.

    Presents a bearer-shaped credential and verifies the placeholder ignores it while retaining
    the challenge Ticket 30 must explicitly replace together with the authenticator.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the placeholder authenticates or loses its challenge.
    """
    request = Request(
        RequestFactory().get(
            "/api/v1/token/logout/",
            HTTP_AUTHORIZATION="Bearer package-presence-must-not-activate",
        )
    )
    authentication = JWTAuthentication()

    assert authentication.authenticate(request) is None
    assert authentication.authenticate_header(request) == "Bearer"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("remote_address", "forwarded", "expected"),
    [
        pytest.param("not-an-address", "198.51.100.1", "not-an-address", id="invalid-peer"),
        pytest.param("10.89.2.5", None, "10.89.2.5", id="trusted-without-forwarding"),
        pytest.param(
            "10.89.2.5",
            "invalid, 198.51.100.44",
            "198.51.100.44",
            id="irrelevant-malformed-left-hop",
        ),
        pytest.param(
            "10.89.2.5",
            "198.51.100.44, invalid",
            "10.89.2.5",
            id="malformed-rightmost-hop",
        ),
        pytest.param(
            "10.89.2.5",
            "198.51.100.44, 10.89.2.6, 10.89.2.7",
            "198.51.100.44",
            id="trusted-proxy-chain",
        ),
        pytest.param(
            "10.89.2.5",
            "10.89.2.6, 10.89.2.7",
            "10.89.2.5",
            id="all-trusted-chain",
        ),
        pytest.param(
            "192.0.2.10",
            "198.51.100.44, 10.89.2.7",
            "192.0.2.10",
            id="untrusted-immediate-peer",
        ),
    ],
)
def test_client_address_resolution_fails_safe(
    remote_address: str,
    forwarded: str | None,
    expected: str,
) -> None:
    """Resolve forwarded chains without trusting malformed or unverified metadata.

    Walks valid hops from the trusted immediate peer toward the client, ignores irrelevant values
    beyond the selected client, and falls back before malformed or proxy-only data can be trusted.

    Arguments:
        remote_address: Immediate WSGI peer value.
        forwarded: Optional forwarded-address header.
        expected: Address the security bucket must use.

    Returns:
        None.

    Raises:
        AssertionError: If a fallback trusts malformed or absent client metadata.
    """
    factory = RequestFactory()
    django_request = (
        factory.post(
            "/api/v1/token/login/",
            REMOTE_ADDR=remote_address,
        )
        if forwarded is None
        else factory.post(
            "/api/v1/token/login/",
            REMOTE_ADDR=remote_address,
            HTTP_X_FORWARDED_FOR=forwarded,
        )
    )
    request = Request(django_request)

    with override_settings(TRUSTED_PROXY_NETWORKS=(ip_network("10.89.2.0/24"),)):
        resolved = trusted_client_address(request)

    assert resolved == expected


@pytest.mark.unit
@pytest.mark.parametrize(
    ("extension_type", "expected"),
    [
        pytest.param(
            JWTAuthenticationScheme,
            {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"},
            id="jwt-scheme",
        ),
        pytest.param(
            PrimaryTokenAuthenticationScheme,
            {
                "type": "apiKey",
                "in": "header",
                "name": "Authorization",
                "description": "Send `Authorization: Token <key>`.",
            },
            id="token-scheme",
        ),
    ],
)
def test_authentication_schema_extensions_publish_their_header_contracts(
    extension_type: type,
    expected: dict[str, str],
) -> None:
    """Describe both configured authentication schemes to OpenAPI.

    Calls each pure schema adapter directly and compares its complete security definition, including
    the runtime token prefix generated clients cannot infer from an API-key header alone.

    Arguments:
        extension_type: Authentication extension class to exercise.
        expected: Independent expected OpenAPI definition.

    Returns:
        None.

    Raises:
        AssertionError: If either security scheme changes or disappears.
    """
    extension = extension_type(object())

    assert extension.get_security_definition(object()) == expected


@pytest.mark.unit
def test_token_values_are_redacted_from_request_logs_and_task_messages() -> None:
    """Remove token values from request context and Celery message metadata.

    Passes one token-shaped value through the structured request formatter and publish-time task
    redactor, proving the two existing escape routes preserve only redacted placeholders.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If either boundary retains the submitted token value.
    """
    token = secrets.token_hex(20)
    request = RequestFactory().post(
        "/api/v1/token/logout/",
        data={"token": token},
        HTTP_AUTHORIZATION=f"Token {token}",
    )
    record = logging.makeLogRecord(
        {
            "name": "localforge.token.probe",
            "levelno": logging.INFO,
            "levelname": "INFO",
            "msg": "request context",
            "request": request,
            "authorization": f"Token {token}",
        }
    )
    rendered = StructuredFormatter().format(record)
    headers = {"argsrepr": repr((token,)), "kwargsrepr": repr({"token": token})}
    redact_published_arguments(
        headers=headers,
        body=((token,), {"token": token}, None),
    )

    assert token not in rendered
    assert json.loads(rendered)["authorization"] == "********"
    assert token not in repr(headers)
    assert headers["kwargsrepr"] == "{'token': '********'}"
    assert settings.CELERY_TASK_SEND_SENT_EVENT is False
