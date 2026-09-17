"""Integration tests for registration and profile endpoints.

Exercises the versioned account HTTP boundaries against PostgreSQL, proving public representations,
account ownership, mutable-field policy, deletion, throttling, and exhaustive schema contracts.
"""

from __future__ import annotations

import secrets
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from http import HTTPStatus
from importlib import import_module
from threading import Barrier
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import parse_qs, urlparse

import pytest
from django.contrib.auth.hashers import PBKDF2PasswordHasher
from django.core import mail
from django.db import DatabaseError, close_old_connections, transaction
from django.db.backends.utils import CursorWrapper
from django.test import Client as DjangoClient
from django.test import override_settings
from rest_framework.authtoken.models import Token

import accounts.user_profiles as user_profiles_module
from accounts.jwt_authentication import PrimaryRefreshToken
from accounts.login_throttle import PostgresLoginThrottleStore
from accounts.models import User
from config.api_errors import ErrorCode, ServiceUnavailable
from config.logs import REQUEST_ID_HEADER

if TYPE_CHECKING:
    from collections.abc import Mapping
    from typing import Protocol

    from django.test import Client

    class ClientResponse(Protocol):
        """Describe public response values asserted by account endpoint tests.

        Inherits from ``Protocol`` and exposes only status, headers, rendered bytes, and JSON
        decoding, avoiding dependence on Django's dynamic test response subtype.

        Attributes:
            status_code: Observed HTTP status.
            headers: Public response header mapping.
            content: Rendered response bytes.

        Members:
            json: Decode the response body as JSON.
        """

        status_code: int
        headers: Mapping[str, str]
        content: bytes

        def json(self) -> object:
            """Decode the rendered response body.

            Uses Django's public JSON helper without naming its dynamic implementation.
            Keeps assertions on the same representation a real API client receives.

            Arguments:
                None.

            Returns:
                Parsed JSON-compatible value.

            Raises:
                ValueError: If the rendered body is not JSON.
            """
            ...


PASSWORD = secrets.token_urlsafe(24)


def _registration_payload(
    username: str,
    *,
    email: str | None = None,
    password: str = PASSWORD,
    password_confirm: str | None = None,
) -> dict[str, str]:
    """Build one complete registration request body.

    Keeps success, duplicate, validation, and throttling tests on the same public field contract
    while allowing each test to vary only the input whose behavior it specifies.

    Arguments:
        username: Public account name to submit.
        email: Optional address, derived from the username when omitted.
        password: Raw credential to submit.
        password_confirm: Optional confirmation, equal to the password when omitted.

    Returns:
        Complete registration request body.
    """
    return {
        "username": username,
        "email": email or f"{username}@localforge.invalid",
        "password": password,
        "password_confirm": password if password_confirm is None else password_confirm,
    }


def _token_headers(account: User) -> dict[str, str]:
    """Create one secondary authentication header for an active account.

    Persists the protocol's single token on the authoritative primary and returns the exact header
    syntax the public profile endpoint accepts.

    Arguments:
        account: Active account whose profile will be exercised.

    Returns:
        Authorization header mapping carrying the account token.
    """
    token = Token.objects.using("default").create(user=account)

    return {"authorization": f"Token {token.key}"}


def _invoke_profile_request(
    client: Client,
    method: str,
    *,
    data: object | None,
    content_type: str,
    headers: dict[str, str] | None,
) -> ClientResponse:
    """Invoke one profile method through Django's public client.

    Supplies a body to GET only when a request-size test needs one, while write methods retain the
    normal JSON path used by clients and every other integration case.

    Arguments:
        client: Django test client issuing the request.
        method: Supported profile method to invoke.
        data: Optional request representation.
        content_type: Media type supplied with the representation.
        headers: Optional authentication and negotiation headers.

    Returns:
        Django test response through its public protocol.
    """
    if method == "get" and data is not None:
        return cast(
            "ClientResponse",
            client.generic(
                "GET",
                "/api/v1/users/me/",
                data=data,
                content_type=content_type,
                headers=headers or {},
            ),
        )

    request = getattr(client, method)
    keywords: dict[str, object] = {}
    if method != "get":
        keywords.update({"data": data, "content_type": content_type})
    if headers is not None:
        keywords["headers"] = headers

    return cast("ClientResponse", request("/api/v1/users/me/", **keywords))


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_registration_creates_an_inactive_account_without_sensitive_output(
    client: Client,
    django_user_model: type[User],
) -> None:
    """Create an inactive account through the public registration boundary.

    Posts the complete registration contract and verifies the accepted representation contains only
    the submitted public identifiers while PostgreSQL holds a hashed credential on an inactive row.

    Arguments:
        client: Django test client issuing the versioned request.
        django_user_model: Configured custom user model class.

    Returns:
        None.

    Raises:
        AssertionError: If registration fails, leaks a sensitive field, or creates an active
            account.
    """
    suffix = uuid.uuid4().hex
    username = f"register-{suffix}"
    email = f"{username}@localforge.invalid"

    response = client.post(
        "/api/v1/users/",
        _registration_payload(username, email=email),
        content_type="application/json",
    )
    payload = cast("dict[str, Any]", response.json())
    account = django_user_model.objects.using("default").get(username=username)

    assert response.status_code == HTTPStatus.CREATED
    assert payload == {"username": username, "email": email}
    assert account.is_active is False
    assert account.check_password(PASSWORD)
    assert PASSWORD.encode() not in response.content


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@pytest.mark.parametrize(
    ("username", "password", "message"),
    [
        pytest.param(
            "similar-registration-password",
            "similar-registration-password",
            "The password is too similar to the username.",
            id="attribute-similarity",
        ),
        pytest.param(
            "minimum-length-probe",
            "A1!",
            "This password is too short. It must contain at least 8 characters.",
            id="minimum-length",
        ),
        pytest.param(
            "common-password-probe",
            "password",
            "This password is too common.",
            id="common-password",
        ),
        pytest.param(
            "numeric-password-probe",
            "123456789",
            "This password is entirely numeric.",
            id="numeric-password",
        ),
    ],
)
def test_registration_reports_each_configured_password_validator_on_the_password_field(
    client: Client,
    username: str,
    password: str,
    message: str,
) -> None:
    """Expose every configured password rejection under the password detail key.

    Posts one request tailored to each configured validator and verifies the shared envelope keeps
    its message attached to the credential field rather than flattening framework validation.

    Arguments:
        client: Django test client issuing the versioned request.
        username: Candidate identifier supplied to similarity validation.
        password: Credential designed to trigger one configured validator.
        message: Independent expected validator message.

    Returns:
        None.

    Raises:
        AssertionError: If a validator is skipped or its error leaves the password detail.
    """
    response = client.post(
        "/api/v1/users/",
        _registration_payload(username, password=password),
        content_type="application/json",
        REMOTE_ADDR=f"2001:db8::{uuid.uuid4().int & 0xFFFF:x}",
    )
    payload = cast("dict[str, Any]", response.json())

    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert payload["code"] == ErrorCode.VALIDATION_ERROR
    assert message in cast("dict[str, list[str]]", payload["details"])["password"]
    assert payload["request_id"] == response.headers[REQUEST_ID_HEADER]


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_registration_reports_confirmation_required_and_email_errors_per_field(
    client: Client,
) -> None:
    """Keep independent registration failures on their submitted field names.

    Exercises missing input, malformed email, and mismatched confirmation through HTTP so clients
    can render each failure without parsing a combined or implementation-specific message.

    Arguments:
        client: Django test client issuing the versioned requests.

    Returns:
        None.

    Raises:
        AssertionError: If validation omits a field or moves its detail elsewhere.
    """
    missing = client.post(
        "/api/v1/users/",
        {},
        content_type="application/json",
        REMOTE_ADDR=f"2001:db8::{uuid.uuid4().int & 0xFFFF:x}",
    )
    invalid_email = client.post(
        "/api/v1/users/",
        _registration_payload(
            f"invalid-registration-{uuid.uuid4().hex}",
            email="not-an-address",
        ),
        content_type="application/json",
        REMOTE_ADDR=f"2001:db8::{uuid.uuid4().int & 0xFFFF:x}",
    )
    mismatched = client.post(
        "/api/v1/users/",
        _registration_payload(
            f"mismatched-registration-{uuid.uuid4().hex}",
            password_confirm=f"{PASSWORD}-different",
        ),
        content_type="application/json",
        REMOTE_ADDR=f"2001:db8::{uuid.uuid4().int & 0xFFFF:x}",
    )
    non_object = client.post(
        "/api/v1/users/",
        data="[]",
        content_type="application/json",
        REMOTE_ADDR=f"2001:db8::{uuid.uuid4().int & 0xFFFF:x}",
    )
    missing_details = cast("dict[str, Any]", missing.json())["details"]
    invalid_email_details = cast("dict[str, Any]", invalid_email.json())["details"]
    mismatched_details = cast("dict[str, Any]", mismatched.json())["details"]

    assert missing.status_code == HTTPStatus.BAD_REQUEST
    assert set(cast("dict[str, object]", missing_details)) == {
        "username",
        "email",
        "password",
        "password_confirm",
    }
    assert invalid_email.status_code == HTTPStatus.BAD_REQUEST
    assert set(cast("dict[str, object]", invalid_email_details)) == {"email"}
    assert mismatched.status_code == HTTPStatus.BAD_REQUEST
    assert set(cast("dict[str, object]", mismatched_details)) == {"password_confirm"}
    assert non_object.status_code == HTTPStatus.BAD_REQUEST
    assert set(cast("dict[str, Any]", non_object.json())["details"]) == {"non_field_errors"}


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_registration_rejects_an_address_made_invalid_by_normalization(
    client: Client,
) -> None:
    """Reject a Unicode address whose normalized form is not a valid email.

    Posts an address that passes initial field parsing but lowercases the capital dotted I into a
    combining sequence, proving the public boundary returns correlated email detail instead of 500.

    Arguments:
        client: Django test client issuing the versioned request.

    Returns:
        None.

    Raises:
        AssertionError: If normalized validation is skipped or escapes the shared envelope.
    """
    response = client.post(
        "/api/v1/users/",
        _registration_payload(
            f"normalized-email-{uuid.uuid4().hex}",
            email="İ@example.com",
        ),
        content_type="application/json",
        REMOTE_ADDR=f"2001:db8::{uuid.uuid4().int & 0xFFFF:x}",
    )
    payload = cast("dict[str, Any]", response.json())

    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert payload["code"] == ErrorCode.VALIDATION_ERROR
    assert set(cast("dict[str, object]", payload["details"])) == {"email"}
    assert payload["request_id"] == response.headers[REQUEST_ID_HEADER]


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_registration_preserves_valid_unicode_email_behavior(client: Client) -> None:
    """Accept and normalize an ordinarily valid Unicode email address.

    Posts a non-ASCII domain whose lowercase form remains valid, proving the second validation
    pass rejects only normalization damage and preserves the established successful behavior.

    Arguments:
        client: Django test client issuing the versioned request.

    Returns:
        None.

    Raises:
        AssertionError: If valid Unicode input is rejected or normalized incorrectly.
    """
    suffix = uuid.uuid4().hex
    username = f"unicode-email-{suffix}"
    email = f"USER-{suffix}@BÜCHER.EXAMPLE"

    response = client.post(
        "/api/v1/users/",
        _registration_payload(username, email=email),
        content_type="application/json",
        REMOTE_ADDR=f"2001:db8::{uuid.uuid4().int & 0xFFFF:x}",
    )

    assert response.status_code == HTTPStatus.CREATED
    assert response.json() == {"username": username, "email": email.lower()}


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@pytest.mark.parametrize("duplicate_field", ["username", "email"])
def test_registration_duplicates_match_success_under_postgresql_lower_semantics(
    client: Client,
    django_user_model: type[User],
    duplicate_field: str,
) -> None:
    """Make case-insensitive duplicate outcomes indistinguishable from creation.

    Seeds one account, submits a case variant of either identifier, and verifies PostgreSQL's
    functional uniqueness changes neither the accepted status nor the submitted public body.

    Arguments:
        client: Django test client issuing the versioned request.
        django_user_model: Configured custom user model class.
        duplicate_field: Identifier whose case-insensitive conflict is exercised.

    Returns:
        None.

    Raises:
        AssertionError: If a duplicate leaks existence or creates another account.
    """
    suffix = uuid.uuid4().hex
    existing_username = f"Existing-{suffix}"
    existing_email = f"Existing-{suffix}@localforge.invalid"
    django_user_model.objects.create_user(existing_username, existing_email, PASSWORD)
    submitted_username = (
        existing_username.swapcase() if duplicate_field == "username" else f"different-{suffix}"
    )
    submitted_email = (
        existing_email.upper()
        if duplicate_field == "email"
        else f"different-{suffix}@localforge.invalid"
    )

    response = client.post(
        "/api/v1/users/",
        _registration_payload(submitted_username, email=submitted_email),
        content_type="application/json",
        REMOTE_ADDR=f"2001:db8::{uuid.uuid4().int & 0xFFFF:x}",
    )

    assert response.status_code == HTTPStatus.CREATED
    assert response.json() == {
        "username": submitted_username,
        "email": submitted_email.lower(),
    }
    assert django_user_model.objects.using("default").count() == 1


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_registration_rejects_privileged_and_unknown_fields(
    client: Client,
) -> None:
    """Reject fields outside the public registration contract.

    Submits privileged account state and an unknown value beside otherwise valid input, proving the
    serializer does not silently ignore fields a caller may mistakenly believe were accepted.

    Arguments:
        client: Django test client issuing the versioned request.

    Returns:
        None.

    Raises:
        AssertionError: If an undeclared field is ignored or accepted.
    """
    username = f"registration-extra-{uuid.uuid4().hex}"
    payload: dict[str, object] = {
        **_registration_payload(username),
        "is_active": True,
        "display_name": "Not a profile field",
    }

    response = client.post(
        "/api/v1/users/",
        payload,
        content_type="application/json",
        REMOTE_ADDR=f"2001:db8::{uuid.uuid4().int & 0xFFFF:x}",
    )
    details = cast("dict[str, Any]", response.json())["details"]

    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert set(cast("dict[str, object]", details)) == {"is_active", "display_name"}


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_registration_is_throttled_authoritatively_and_recovers(
    client: Client,
) -> None:
    """Reject registration above the configured address rate and recover after its window.

    Sends three distinct valid registrations from one address around a one-second rolling window,
    proving primary-backed admission returns Retry-After and later admits without cache state.

    Arguments:
        client: Django test client issuing the versioned requests.

    Returns:
        None.

    Raises:
        AssertionError: If the limit is skipped, lacks correlation, or never recovers.
    """
    remote_address = f"2001:db8::{uuid.uuid4().int & 0xFFFF:x}"

    with override_settings(USER_REGISTRATION_ADDRESS_THROTTLE_RATE="1/second"):
        first = client.post(
            "/api/v1/users/",
            _registration_payload(f"registration-first-{uuid.uuid4().hex}"),
            content_type="application/json",
            REMOTE_ADDR=remote_address,
        )
        throttled = client.post(
            "/api/v1/users/",
            _registration_payload(f"registration-throttled-{uuid.uuid4().hex}"),
            content_type="application/json",
            REMOTE_ADDR=remote_address,
        )
        time.sleep(1.1)
        recovered = client.post(
            "/api/v1/users/",
            _registration_payload(f"registration-recovered-{uuid.uuid4().hex}"),
            content_type="application/json",
            REMOTE_ADDR=remote_address,
        )

    throttled_payload = cast("dict[str, Any]", throttled.json())

    assert first.status_code == HTTPStatus.CREATED
    assert throttled.status_code == HTTPStatus.TOO_MANY_REQUESTS
    assert int(throttled.headers["Retry-After"]) >= 1
    assert throttled_payload["code"] == ErrorCode.THROTTLED
    assert throttled_payload["request_id"] == throttled.headers[REQUEST_ID_HEADER]
    assert recovered.status_code == HTTPStatus.CREATED


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_registration_throttle_records_only_post_attempts(client: Client) -> None:
    """Exclude metadata and unsupported methods from registration admission.

    Exercises GET, HEAD, OPTIONS, and PUT from one address at an exact one-request limit. The first
    POST remains admitted while the second POST alone exhausts the address scope.

    Arguments:
        client: Django test client issuing the versioned requests.

    Returns:
        None.

    Raises:
        AssertionError: If a non-POST consumes quota or POST admission is not authoritative.
    """
    remote_address = f"2001:db8::{uuid.uuid4().int & 0xFFFF:x}"

    with override_settings(USER_REGISTRATION_ADDRESS_THROTTLE_RATE="1/minute"):
        get_response = client.get("/api/v1/users/", REMOTE_ADDR=remote_address)
        head_response = client.head("/api/v1/users/", REMOTE_ADDR=remote_address)
        options_response = client.options("/api/v1/users/", REMOTE_ADDR=remote_address)
        put_response = client.put(
            "/api/v1/users/",
            {},
            content_type="application/json",
            REMOTE_ADDR=remote_address,
        )
        admitted = client.post(
            "/api/v1/users/",
            _registration_payload(f"registration-method-admitted-{uuid.uuid4().hex}"),
            content_type="application/json",
            REMOTE_ADDR=remote_address,
        )
        throttled = client.post(
            "/api/v1/users/",
            _registration_payload(f"registration-method-throttled-{uuid.uuid4().hex}"),
            content_type="application/json",
            REMOTE_ADDR=remote_address,
        )

    assert get_response.status_code == HTTPStatus.METHOD_NOT_ALLOWED
    assert head_response.status_code == HTTPStatus.METHOD_NOT_ALLOWED
    assert options_response.status_code == HTTPStatus.OK
    assert put_response.status_code == HTTPStatus.METHOD_NOT_ALLOWED
    assert admitted.status_code == HTTPStatus.CREATED
    assert throttled.status_code == HTTPStatus.TOO_MANY_REQUESTS
    assert cast("dict[str, Any]", throttled.json())["code"] == ErrorCode.THROTTLED


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_profile_returns_only_the_authenticated_caller_and_ignores_query_identifiers(
    client: Client,
    django_user_model: type[User],
) -> None:
    """Return the caller even when another account identifier is supplied.

    Authenticates one account, places a different account key in the query string, and verifies the
    fixed self-profile route has no lookup input capable of selecting another row.

    Arguments:
        client: Django test client issuing the versioned request.
        django_user_model: Configured custom user model class.

    Returns:
        None.

    Raises:
        AssertionError: If another account can be selected or extra fields are exposed.
    """
    suffix = uuid.uuid4().hex
    caller = django_user_model.objects.create_user(
        f"profile-caller-{suffix}",
        f"profile-caller-{suffix}@localforge.invalid",
        PASSWORD,
        is_active=True,
    )
    other = django_user_model.objects.create_user(
        f"profile-other-{suffix}",
        f"profile-other-{suffix}@localforge.invalid",
        PASSWORD,
        is_active=True,
    )

    response = client.get(
        f"/api/v1/users/me/?id={other.pk}",
        headers=_token_headers(caller),
    )

    assert response.status_code == HTTPStatus.OK
    assert response.json() == {
        "id": str(caller.pk),
        "username": caller.username,
        "email": caller.email,
    }
    assert other.email.encode() not in response.content


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_profile_partial_update_changes_only_the_email(
    client: Client,
    django_user_model: type[User],
) -> None:
    """Update the sole mutable profile field without changing omitted state.

    Patches an uppercase address through the public route and verifies normalization, response
    shape, and preservation of the account identifier, username, password, and permission flags.

    Arguments:
        client: Django test client issuing the versioned request.
        django_user_model: Configured custom user model class.

    Returns:
        None.

    Raises:
        AssertionError: If the patch changes an omitted or immutable account field.
    """
    suffix = uuid.uuid4().hex
    account = django_user_model.objects.create_user(
        f"profile-update-{suffix}",
        f"profile-update-{suffix}@localforge.invalid",
        PASSWORD,
        is_active=True,
    )
    original_id = account.pk
    original_username = account.username
    original_password = account.password
    updated_email = f"UPDATED-{suffix}@LOCALFORGE.INVALID"
    headers = _token_headers(account)

    response = client.patch(
        "/api/v1/users/me/",
        {"email": updated_email},
        content_type="application/json",
        headers=headers,
    )
    unchanged = client.patch(
        "/api/v1/users/me/",
        {},
        content_type="application/json",
        headers=headers,
    )
    account.refresh_from_db(using="default")

    assert response.status_code == HTTPStatus.OK
    assert unchanged.status_code == HTTPStatus.OK
    assert response.json() == {
        "id": str(original_id),
        "username": original_username,
        "email": updated_email.lower(),
    }
    assert account.pk == original_id
    assert account.username == original_username
    assert account.email == updated_email.lower()
    assert account.password == original_password
    assert account.is_active is True
    assert account.is_staff is False
    assert account.is_superuser is False


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_profile_update_rejects_an_address_made_invalid_by_normalization(
    client: Client,
    django_user_model: type[User],
) -> None:
    """Reject a Unicode profile address whose normalized form is invalid.

    Patches an address that initially parses but lowercases into a combining sequence, proving the
    public boundary returns correlated email detail and leaves the authoritative row unchanged.

    Arguments:
        client: Django test client issuing the versioned request.
        django_user_model: Configured custom user model class.

    Returns:
        None.

    Raises:
        AssertionError: If normalized validation is skipped, escapes, or mutates the account.
    """
    suffix = uuid.uuid4().hex
    original_email = f"profile-normalized-{suffix}@localforge.invalid"
    account = django_user_model.objects.create_user(
        f"profile-normalized-{suffix}",
        original_email,
        PASSWORD,
        is_active=True,
    )

    response = client.patch(
        "/api/v1/users/me/",
        {"email": "İ@example.com"},
        content_type="application/json",
        headers=_token_headers(account),
    )
    account.refresh_from_db(using="default")
    payload = cast("dict[str, Any]", response.json())

    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert payload["code"] == ErrorCode.VALIDATION_ERROR
    assert set(cast("dict[str, object]", payload["details"])) == {"email"}
    assert payload["request_id"] == response.headers[REQUEST_ID_HEADER]
    assert account.email == original_email


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_registration_persistence_outage_returns_service_unavailable(
    client: Client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail registration closed when authoritative account persistence is unavailable.

    Replaces the model write boundary with a database failure and verifies the public request
    receives the correlated service-unavailable envelope rather than an accepted response.

    Arguments:
        client: Django test client issuing the versioned request.
        monkeypatch: Fixture inducing authoritative persistence loss.

    Returns:
        None.

    Raises:
        AssertionError: If database loss is accepted or escapes the shared envelope.
    """

    def fail_save(_account: User, *args: object, **kwargs: object) -> None:
        """Raise one authoritative account-write failure.

        Replaces only model persistence after password validation and hashing have completed.
        Keeps request parsing, validation, throttling, and error conversion on their real paths.

        Arguments:
            _account: Candidate account being persisted.
            *args: Positional model-save arguments.
            **kwargs: Keyword model-save arguments.

        Returns:
            Never returns.

        Raises:
            DatabaseError: Always, representing primary database loss.
        """
        del args, kwargs
        raise DatabaseError

    monkeypatch.setattr(User, "save", fail_save)
    response = client.post(
        "/api/v1/users/",
        _registration_payload(f"registration-persistence-{uuid.uuid4().hex}"),
        content_type="application/json",
        REMOTE_ADDR=f"2001:db8::{uuid.uuid4().int & 0xFFFF:x}",
    )

    assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert cast("dict[str, Any]", response.json())["code"] == ErrorCode.SERVICE_UNAVAILABLE


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@pytest.mark.parametrize("method", ["get", "patch", "delete"])
def test_profile_token_query_outage_returns_service_unavailable(
    client: Client,
    django_user_model: type[User],
    monkeypatch: pytest.MonkeyPatch,
    method: str,
) -> None:
    """Fail every profile operation closed when the primary token query is unavailable.

    Breaks the real authoritative token SELECT below Django's ORM and verifies authentication maps
    database loss to the documented correlated response before any profile method can execute.

    Arguments:
        client: Django test client issuing the versioned request.
        django_user_model: Configured custom user model class.
        monkeypatch: Fixture inducing the primary token-query failure.
        method: Profile operation attempted with the persisted token.

    Returns:
        None.

    Raises:
        AssertionError: If query loss escapes authentication or becomes another public status.
    """
    suffix = uuid.uuid4().hex
    account = django_user_model.objects.create_user(
        f"profile-auth-outage-{method}-{suffix}",
        f"profile-auth-outage-{method}-{suffix}@localforge.invalid",
        PASSWORD,
        is_active=True,
    )
    headers = _token_headers(account)
    original_execute = CursorWrapper.execute

    def fail_token_select(
        wrapper: CursorWrapper,
        sql: str,
        params: object = None,
    ) -> object:
        """Raise only for the authoritative token-and-account query.

        Allows middleware and response handling to retain their normal database behavior while the
        authentication query itself fails at the driver boundary.

        Arguments:
            wrapper: Django database cursor wrapper executing the statement.
            sql: SQL statement generated by the ORM.
            params: Optional statement parameters.

        Returns:
            Result from every unrelated statement.

        Raises:
            DatabaseError: If token authentication executes its primary SELECT.
        """
        if "authtoken_token" in sql and sql.lstrip().upper().startswith("SELECT"):
            raise DatabaseError

        return original_execute(wrapper, sql, cast("Any", params))

    monkeypatch.setattr(CursorWrapper, "execute", fail_token_select)
    client.raise_request_exception = False
    data: object | None = None
    if method == "patch":
        data = {"email": f"profile-auth-outage-updated-{suffix}@localforge.invalid"}
    elif method == "delete":
        data = {"current_password": PASSWORD}

    response = _invoke_profile_request(
        client,
        method,
        data=data,
        content_type="application/json",
        headers=headers,
    )
    payload = cast("dict[str, Any]", response.json())

    assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert payload["code"] == ErrorCode.SERVICE_UNAVAILABLE
    assert payload["request_id"] == response.headers[REQUEST_ID_HEADER]


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@pytest.mark.parametrize("method", ["patch", "delete"])
def test_profile_persistence_outage_returns_service_unavailable(
    client: Client,
    django_user_model: type[User],
    monkeypatch: pytest.MonkeyPatch,
    method: str,
) -> None:
    """Fail profile mutation closed when authoritative persistence is unavailable.

    Replaces the selected model mutation after authentication and validation, proving both profile
    writes expose dependency loss through the shared correlated service-unavailable response.

    Arguments:
        client: Django test client issuing the versioned request.
        django_user_model: Configured custom user model class.
        monkeypatch: Fixture inducing authoritative persistence loss.
        method: Profile mutation whose database boundary is replaced.

    Returns:
        None.

    Raises:
        AssertionError: If a database outage is accepted or becomes another public status.
    """
    suffix = uuid.uuid4().hex
    account = django_user_model.objects.create_user(
        f"profile-outage-{method}-{suffix}",
        f"profile-outage-{method}-{suffix}@localforge.invalid",
        PASSWORD,
        is_active=True,
    )
    data = (
        {"email": f"profile-outage-updated-{suffix}@localforge.invalid"}
        if method == "patch"
        else {"current_password": PASSWORD}
    )

    def fail_mutation(_account: User, *args: object, **kwargs: object) -> None:
        """Raise one authoritative profile-write failure.

        Replaces only the model mutation selected by the parametrized public operation.
        Keeps authentication, request validation, and error conversion on their real paths.

        Arguments:
            _account: Authenticated account being mutated.
            *args: Positional model mutation arguments.
            **kwargs: Keyword model mutation arguments.

        Returns:
            Never returns.

        Raises:
            DatabaseError: Always, representing primary database loss.
        """
        del args, kwargs
        raise DatabaseError

    monkeypatch.setattr(
        User,
        "save" if method == "patch" else "delete",
        fail_mutation,
    )
    response = _invoke_profile_request(
        client,
        method,
        data=data,
        content_type="application/json",
        headers=_token_headers(account),
    )

    assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert cast("dict[str, Any]", response.json())["code"] == ErrorCode.SERVICE_UNAVAILABLE


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_profile_rejects_identifier_password_and_permission_updates(
    client: Client,
    django_user_model: type[User],
) -> None:
    """Reject every account field outside the mutable profile contract.

    Submits the immutable key, username, credential, activation state, and permission state
    together and verifies each receives field detail while the authoritative row remains unchanged.

    Arguments:
        client: Django test client issuing the versioned request.
        django_user_model: Configured custom user model class.

    Returns:
        None.

    Raises:
        AssertionError: If a forbidden field is ignored, accepted, or persisted.
    """
    suffix = uuid.uuid4().hex
    account = django_user_model.objects.create_user(
        f"profile-forbidden-{suffix}",
        f"profile-forbidden-{suffix}@localforge.invalid",
        PASSWORD,
        is_active=True,
    )
    original = {
        "id": account.pk,
        "username": account.username,
        "email": account.email,
        "password": account.password,
        "is_active": account.is_active,
        "is_staff": account.is_staff,
        "is_superuser": account.is_superuser,
    }
    forbidden = {
        "id": str(uuid.uuid4()),
        "username": f"replaced-{suffix}",
        "password": f"{PASSWORD}-replacement",
        "is_active": False,
        "is_staff": True,
        "is_superuser": True,
        "groups": [],
        "user_permissions": [],
    }

    response = client.patch(
        "/api/v1/users/me/",
        forbidden,
        content_type="application/json",
        headers=_token_headers(account),
    )
    account.refresh_from_db(using="default")
    details = cast("dict[str, Any]", response.json())["details"]

    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert set(cast("dict[str, object]", details)) == set(forbidden)
    assert account.pk == original["id"]
    assert account.username == original["username"]
    assert account.email == original["email"]
    assert account.password == original["password"]
    assert account.is_active is original["is_active"]
    assert account.is_staff is original["is_staff"]
    assert account.is_superuser is original["is_superuser"]


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_profile_email_update_uses_postgresql_lower_uniqueness(
    client: Client,
    django_user_model: type[User],
) -> None:
    """Reject a case-insensitive email conflict as field validation.

    Updates one caller toward another account's differently cased address and verifies PostgreSQL's
    authoritative uniqueness result is contained in the standard validation envelope.

    Arguments:
        client: Django test client issuing the versioned request.
        django_user_model: Configured custom user model class.

    Returns:
        None.

    Raises:
        AssertionError: If the conflict becomes a server error or overwrites either account.
    """
    suffix = uuid.uuid4().hex
    occupied_email = f"occupied-{suffix}@localforge.invalid"
    account = django_user_model.objects.create_user(
        f"profile-conflict-{suffix}",
        f"profile-conflict-{suffix}@localforge.invalid",
        PASSWORD,
        is_active=True,
    )
    django_user_model.objects.create_user(
        f"profile-occupied-{suffix}",
        occupied_email,
        PASSWORD,
        is_active=True,
    )

    response = client.patch(
        "/api/v1/users/me/",
        {"email": occupied_email.upper()},
        content_type="application/json",
        headers=_token_headers(account),
    )
    account.refresh_from_db(using="default")
    payload = cast("dict[str, Any]", response.json())

    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert payload["code"] == ErrorCode.VALIDATION_ERROR
    assert set(cast("dict[str, object]", payload["details"])) == {"email"}
    assert account.email == f"profile-conflict-{suffix}@localforge.invalid"


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_profile_deletion_requires_the_current_password(
    client: Client,
    django_user_model: type[User],
) -> None:
    """Retain the account when deletion lacks a matching current password.

    Exercises missing and incorrect credentials through the deletion boundary and verifies each
    receives field validation while the account remains available to its existing token.

    Arguments:
        client: Django test client issuing the versioned requests.
        django_user_model: Configured custom user model class.

    Returns:
        None.

    Raises:
        AssertionError: If deletion proceeds without the current credential.
    """
    suffix = uuid.uuid4().hex
    account = django_user_model.objects.create_user(
        f"profile-delete-guard-{suffix}",
        f"profile-delete-guard-{suffix}@localforge.invalid",
        PASSWORD,
        is_active=True,
    )
    headers = _token_headers(account)

    missing = client.delete(
        "/api/v1/users/me/",
        data={},
        content_type="application/json",
        headers=headers,
    )
    wrong = client.delete(
        "/api/v1/users/me/",
        data={"current_password": f"{PASSWORD}-wrong"},
        content_type="application/json",
        headers=headers,
    )
    retained = client.get("/api/v1/users/me/", headers=headers)

    assert missing.status_code == HTTPStatus.BAD_REQUEST
    assert set(cast("dict[str, Any]", missing.json())["details"]) == {"current_password"}
    assert wrong.status_code == HTTPStatus.BAD_REQUEST
    assert set(cast("dict[str, Any]", wrong.json())["details"]) == {"current_password"}
    assert retained.status_code == HTTPStatus.OK
    assert django_user_model.objects.using("default").filter(pk=account.pk).exists()


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@pytest.mark.timeout(30)
def test_profile_deletion_verifies_the_password_from_the_locked_account(
    django_user_model: type[User],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject an old password after a concurrent password change commits.

    Holds the account lock on a separate primary connection until authentication has read the old
    row, then commits a new password before deletion can lock and verify authoritative state.

    Arguments:
        django_user_model: Configured custom user model class.
        monkeypatch: Fixture synchronizing the authentication query with the concurrent commit.

    Returns:
        None.

    Raises:
        AssertionError: If deletion verifies stale authentication state, removes the account, or
            mutates the concurrently committed password.
    """
    suffix = uuid.uuid4().hex
    account = django_user_model.objects.create_user(
        f"profile-delete-race-{suffix}",
        f"profile-delete-race-{suffix}@localforge.invalid",
        PASSWORD,
        is_active=True,
    )
    headers = _token_headers(account)
    replacement_password = secrets.token_urlsafe(24)
    lock_ready = Barrier(2)
    authentication_read = Barrier(2)
    original_execute = CursorWrapper.execute
    authentication_synchronized = False

    def synchronize_authentication(
        wrapper: CursorWrapper,
        sql: str,
        params: object = None,
    ) -> object:
        """Release the password change after authentication reads the old account row.

        Delegates the real token-and-account query before meeting the concurrent transaction, so
        its result is fixed to the committed password that preceded the lock holder's update.

        Arguments:
            wrapper: Django database cursor wrapper executing the statement.
            sql: SQL statement generated by the ORM.
            params: Optional statement parameters.

        Returns:
            Result from the delegated database statement.
        """
        nonlocal authentication_synchronized
        result = original_execute(wrapper, sql, cast("Any", params))
        if (
            not authentication_synchronized
            and "authtoken_token" in sql
            and sql.lstrip().upper().startswith("SELECT")
        ):
            authentication_synchronized = True
            authentication_read.wait()

        return result

    def change_password() -> str:
        """Commit a replacement password from an independent primary connection.

        Locks and updates the account before the deletion request authenticates, then retains the
        lock until that request has completed its non-locking authentication read.

        Arguments:
            None.

        Returns:
            Exact encoded password committed by the concurrent transaction.
        """
        close_old_connections()
        try:
            with transaction.atomic(using="default"):
                locked_account = (
                    django_user_model.objects.using("default")
                    .select_for_update()
                    .get(pk=account.pk)
                )
                locked_account.set_password(replacement_password)
                locked_account.save(
                    using="default",
                    update_fields=["password", "updated_at"],
                )
                encoded_password = locked_account.password
                lock_ready.wait()
                authentication_read.wait()

            return encoded_password
        finally:
            close_old_connections()

    monkeypatch.setattr(CursorWrapper, "execute", synchronize_authentication)
    with ThreadPoolExecutor(max_workers=1) as executor:
        changed_password = executor.submit(change_password)
        lock_ready.wait()
        response = DjangoClient().delete(
            "/api/v1/users/me/",
            data={"current_password": PASSWORD},
            content_type="application/json",
            headers=headers,
        )
        committed_password = changed_password.result()

    retained = django_user_model.objects.using("default").get(pk=account.pk)
    payload = cast("dict[str, Any]", response.json())

    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert payload["code"] == ErrorCode.VALIDATION_ERROR
    assert payload["details"] == {"current_password": ["The current password is incorrect."]}
    assert retained.password == committed_password


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@pytest.mark.timeout(30)
def test_profile_deletion_hides_an_account_removed_after_authentication(
    django_user_model: type[User],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Return generic authentication failure when the authenticated row disappears.

    Deletes the account on a separate primary connection after token authentication has read it,
    proving the deletion transaction handles the missing locked row without identity disclosure.

    Arguments:
        django_user_model: Configured custom user model class.
        monkeypatch: Fixture synchronizing authentication with the concurrent deletion commit.

    Returns:
        None.

    Raises:
        AssertionError: If the race escapes as a server error or discloses account identity.
    """
    suffix = uuid.uuid4().hex
    account = django_user_model.objects.create_user(
        f"profile-delete-missing-{suffix}",
        f"profile-delete-missing-{suffix}@localforge.invalid",
        PASSWORD,
        is_active=True,
    )
    username = account.username
    email = account.email
    headers = _token_headers(account)
    lock_ready = Barrier(2)
    authentication_read = Barrier(2)
    original_execute = CursorWrapper.execute
    authentication_synchronized = False

    def synchronize_authentication(
        wrapper: CursorWrapper,
        sql: str,
        params: object = None,
    ) -> object:
        """Release concurrent deletion after authentication reads the account.

        Delegates the real token query before releasing the lock holder, preserving the race in
        which authentication succeeds but the profile deletion transaction finds no account.

        Arguments:
            wrapper: Django database cursor wrapper executing the statement.
            sql: SQL statement generated by the ORM.
            params: Optional statement parameters.

        Returns:
            Result from the delegated database statement.
        """
        nonlocal authentication_synchronized
        result = original_execute(wrapper, sql, cast("Any", params))
        if (
            not authentication_synchronized
            and "authtoken_token" in sql
            and sql.lstrip().upper().startswith("SELECT")
        ):
            authentication_synchronized = True
            authentication_read.wait()

        return result

    def remove_account() -> None:
        """Delete the account from an independent primary connection.

        Locks and deletes the row before the request authenticates, then commits only after the
        authentication query has fixed its result to the previously visible account.

        Arguments:
            None.

        Returns:
            None.
        """
        close_old_connections()
        try:
            with transaction.atomic(using="default"):
                locked_account = (
                    django_user_model.objects.using("default")
                    .select_for_update()
                    .get(pk=account.pk)
                )
                locked_account.delete(using="default")
                lock_ready.wait()
                authentication_read.wait()
        finally:
            close_old_connections()

    monkeypatch.setattr(CursorWrapper, "execute", synchronize_authentication)
    with ThreadPoolExecutor(max_workers=1) as executor:
        removed_account = executor.submit(remove_account)
        lock_ready.wait()
        response = DjangoClient().delete(
            "/api/v1/users/me/",
            data={"current_password": PASSWORD},
            content_type="application/json",
            headers=headers,
        )
        removed_account.result()

    payload = cast("dict[str, Any]", response.json())

    assert response.status_code == HTTPStatus.UNAUTHORIZED
    assert payload["code"] == ErrorCode.AUTHENTICATION_FAILED
    assert payload["request_id"] == response.headers[REQUEST_ID_HEADER]
    assert username.encode() not in response.content
    assert email.encode() not in response.content
    assert not django_user_model.objects.using("default").filter(pk=account.pk).exists()


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_failed_profile_deletion_does_not_rehash_an_outdated_password(
    client: Client,
    django_user_model: type[User],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Preserve an accepted outdated password when account deletion fails.

    Stores a lower-iteration PBKDF2 credential, verifies it through the public deletion request,
    then fails the authoritative delete and proves neither password nor account was mutated.

    Arguments:
        client: Django test client issuing the versioned request.
        django_user_model: Configured custom user model class.
        monkeypatch: Fixture inducing the authoritative deletion failure.

    Returns:
        None.

    Raises:
        AssertionError: If verification rehashes the doomed account or deletion loss is misreported.
    """
    suffix = uuid.uuid4().hex
    account = django_user_model.objects.create_user(
        f"profile-delete-outdated-{suffix}",
        f"profile-delete-outdated-{suffix}@localforge.invalid",
        PASSWORD,
        is_active=True,
    )
    hasher = PBKDF2PasswordHasher()
    outdated_password = hasher.encode(PASSWORD, hasher.salt(), iterations=1000)
    account.password = outdated_password
    account.save(using="default", update_fields=["password", "updated_at"])
    headers = _token_headers(account)

    def fail_delete(_account: User, *args: object, **kwargs: object) -> None:
        """Raise one authoritative account-deletion failure.

        Replaces only the final model deletion so password verification and transaction handling
        remain on their real public-request path.

        Arguments:
            _account: Authenticated account selected for deletion.
            *args: Positional deletion arguments.
            **kwargs: Keyword deletion arguments.

        Returns:
            Never returns.

        Raises:
            DatabaseError: Always, representing primary deletion loss.
        """
        del args, kwargs
        raise DatabaseError

    monkeypatch.setattr(User, "delete", fail_delete)
    response = client.delete(
        "/api/v1/users/me/",
        data={"current_password": PASSWORD},
        content_type="application/json",
        headers=headers,
    )
    retained = django_user_model.objects.using("default").get(pk=account.pk)

    assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert cast("dict[str, Any]", response.json())["code"] == ErrorCode.SERVICE_UNAVAILABLE
    assert retained.password == outdated_password


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_profile_deletion_accepts_an_outdated_pbkdf2_password(
    client: Client,
    django_user_model: type[User],
) -> None:
    """Delete an account after hash-level verification of an outdated password.

    Stores a lower-iteration PBKDF2 credential and submits its matching raw value through the public
    boundary, proving verification remains compatible without requiring a pre-deletion rehash.

    Arguments:
        client: Django test client issuing the versioned request.
        django_user_model: Configured custom user model class.

    Returns:
        None.

    Raises:
        AssertionError: If the accepted outdated credential cannot authorize deletion.
    """
    suffix = uuid.uuid4().hex
    account = django_user_model.objects.create_user(
        f"profile-delete-legacy-{suffix}",
        f"profile-delete-legacy-{suffix}@localforge.invalid",
        PASSWORD,
        is_active=True,
    )
    hasher = PBKDF2PasswordHasher()
    account.password = hasher.encode(PASSWORD, hasher.salt(), iterations=1000)
    account.save(using="default", update_fields=["password", "updated_at"])

    response = client.delete(
        "/api/v1/users/me/",
        data={"current_password": PASSWORD},
        content_type="application/json",
        headers=_token_headers(account),
    )

    assert response.status_code == HTTPStatus.NO_CONTENT
    assert not django_user_model.objects.using("default").filter(pk=account.pk).exists()


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_profile_deletion_is_irreversible_and_invalidates_token_and_jwt_authentication(
    client: Client,
    django_user_model: type[User],
) -> None:
    """Delete the account and reject every previously issued credential.

    Authenticates deletion with the secondary token, then retries the profile with that token, its
    JWT access credential, and its refresh credential to prove mutable account state is
    authoritative.

    Arguments:
        client: Django test client issuing the versioned requests.
        django_user_model: Configured custom user model class.

    Returns:
        None.

    Raises:
        AssertionError: If the row survives or any prior credential remains usable.
    """
    suffix = uuid.uuid4().hex
    account = django_user_model.objects.create_user(
        f"profile-delete-{suffix}",
        f"profile-delete-{suffix}@localforge.invalid",
        PASSWORD,
        is_active=True,
    )
    token_headers = _token_headers(account)
    refresh = PrimaryRefreshToken.for_user(account)
    access_headers = {"authorization": f"Bearer {refresh.access_token}"}

    deleted = client.delete(
        "/api/v1/users/me/",
        data={"current_password": PASSWORD},
        content_type="application/json",
        headers=token_headers,
    )
    token_reuse = client.get("/api/v1/users/me/", headers=token_headers)
    jwt_reuse = client.get("/api/v1/users/me/", headers=access_headers)
    refresh_reuse = client.post(
        "/api/v1/jwt/refresh/",
        {"refresh": str(refresh)},
        content_type="application/json",
    )

    assert deleted.status_code == HTTPStatus.NO_CONTENT
    assert deleted.content == b""
    assert not django_user_model.objects.using("default").filter(pk=account.pk).exists()
    assert token_reuse.status_code == HTTPStatus.UNAUTHORIZED
    assert jwt_reuse.status_code == HTTPStatus.UNAUTHORIZED
    assert refresh_reuse.status_code == HTTPStatus.UNAUTHORIZED


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_ordinary_callers_cannot_list_accounts(
    client: Client,
    django_user_model: type[User],
) -> None:
    """Keep the registration collection from becoming an account listing.

    Authenticates an ordinary account and requests the collection with GET, verifying the route
    remains method-only registration and cannot return another account's representation.

    Arguments:
        client: Django test client issuing the versioned request.
        django_user_model: Configured custom user model class.

    Returns:
        None.

    Raises:
        AssertionError: If an ordinary caller receives account data.
    """
    suffix = uuid.uuid4().hex
    caller = django_user_model.objects.create_user(
        f"profile-list-caller-{suffix}",
        f"profile-list-caller-{suffix}@localforge.invalid",
        PASSWORD,
        is_active=True,
    )
    other = django_user_model.objects.create_user(
        f"profile-list-other-{suffix}",
        f"profile-list-other-{suffix}@localforge.invalid",
        PASSWORD,
        is_active=True,
    )

    response = client.get("/api/v1/users/", headers=_token_headers(caller))

    assert response.status_code == HTTPStatus.METHOD_NOT_ALLOWED
    assert cast("dict[str, Any]", response.json())["code"] == ErrorCode.METHOD_NOT_ALLOWED
    assert other.email.encode() not in response.content


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@pytest.mark.parametrize("method", ["get", "patch", "delete"])
def test_unauthenticated_profile_access_is_correlated_unauthorized(
    client: Client,
    method: str,
) -> None:
    """Reject anonymous profile operations without redirecting to a login page.

    Calls each supported self-profile method without a credential and verifies the shared
    unauthorized envelope and correlation header are returned directly.

    Arguments:
        client: Django test client issuing the versioned request.
        method: Supported HTTP operation invoked without authentication.

    Returns:
        None.

    Raises:
        AssertionError: If anonymous access redirects or uses another error contract.
    """
    request = getattr(client, method)
    keywords: dict[str, object] = {}
    if method != "get":
        keywords = {"data": {}, "content_type": "application/json"}

    response = request("/api/v1/users/me/", **keywords)
    payload = cast("dict[str, Any]", response.json())

    assert response.status_code == HTTPStatus.UNAUTHORIZED
    assert "Location" not in response.headers
    assert payload["code"] == ErrorCode.NOT_AUTHENTICATED
    assert payload["request_id"] == response.headers[REQUEST_ID_HEADER]


@pytest.mark.integration
@pytest.mark.services("postgres")
def test_registration_and_profile_routes_document_every_reachable_response() -> None:
    """Expose complete registration and self-profile contracts in OpenAPI.

    Generates the public schema and verifies each supported method lists every successful,
    framework, dependency, and failure outcome with examples and the decided field boundaries.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If a route, method, status, example, field, or retention statement is
            missing.
    """
    generator = import_module("drf_spectacular.generators").SchemaGenerator()
    schema = cast("dict[str, Any]", generator.get_schema(request=None, public=True))
    paths = cast("dict[str, Any]", schema["paths"])
    registration = cast("dict[str, Any]", paths["/api/v1/users/"])
    profile = cast("dict[str, Any]", paths["/api/v1/users/me/"])
    expected = {
        "registration": (
            cast("dict[str, Any]", registration["post"]),
            {"201", "204", "400", "405", "406", "413", "415", "429", "500", "503"},
        ),
        "profile-get": (
            cast("dict[str, Any]", profile["get"]),
            {"200", "401", "405", "406", "413", "500", "503"},
        ),
        "profile-patch": (
            cast("dict[str, Any]", profile["patch"]),
            {"200", "400", "401", "405", "406", "413", "415", "500", "503"},
        ),
        "profile-delete": (
            cast("dict[str, Any]", profile["delete"]),
            {"204", "400", "401", "405", "406", "413", "415", "500", "503"},
        ),
    }

    assert set(registration) == {"post"}
    assert set(profile) == {"get", "patch", "delete"}

    for operation, statuses in expected.values():
        responses = cast("dict[str, Any]", operation["responses"])

        assert set(responses) == statuses
        for status, response in responses.items():
            if status == "204":
                continue

            content = cast("dict[str, Any]", response["content"])
            examples = cast("dict[str, Any]", content["application/json"]["examples"])

            assert examples

    components = cast("dict[str, Any]", schema["components"])["schemas"]
    registration_request = cast(
        "dict[str, Any]",
        cast("dict[str, Any]", components)["Registration"],
    )
    profile_update = cast(
        "dict[str, Any]",
        cast("dict[str, Any]", components)["PatchedUserProfileUpdate"],
    )
    registration_description = cast("str", registration["post"]["description"])
    deletion_description = cast(
        "str",
        cast("dict[str, Any]", profile["delete"]["responses"])["204"]["description"],
    )

    assert set(cast("dict[str, object]", registration_request["properties"])) == {
        "username",
        "email",
        "password",
        "password_confirm",
    }
    assert set(cast("dict[str, object]", profile_update["properties"])) == {"email"}
    assert "same status and body" in registration_description
    assert "activation" in deletion_description
    assert "JWT revocation metadata" in deletion_description
    assert "logs" in deletion_description
    assert "backups" in deletion_description


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_registration_observed_statuses_exactly_match_its_documented_contract(
    client: Client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Compare every empirically reachable registration status with OpenAPI.

    Exercises success, validation, routing, negotiation, size, representation, throttling,
    dependency loss, and unexpected failure through the public versioned endpoint.

    Arguments:
        client: Django test client issuing the versioned requests.
        monkeypatch: Fixture inducing documented dependency and unexpected failures.

    Returns:
        None.

    Raises:
        AssertionError: If observed and documented registration statuses differ.
    """
    high_rate = "1000/minute"
    with override_settings(USER_REGISTRATION_ADDRESS_THROTTLE_RATE=high_rate):
        success = client.post(
            "/api/v1/users/",
            _registration_payload(f"registration-contract-{uuid.uuid4().hex}"),
            content_type="application/json",
            REMOTE_ADDR=f"2001:db8::{uuid.uuid4().int & 0xFFFF:x}",
        )
        invalid = client.post(
            "/api/v1/users/",
            {},
            content_type="application/json",
            REMOTE_ADDR=f"2001:db8::{uuid.uuid4().int & 0xFFFF:x}",
        )
        method_not_allowed = client.get("/api/v1/users/")
        not_acceptable = client.post(
            "/api/v1/users/",
            _registration_payload(f"registration-negotiation-{uuid.uuid4().hex}"),
            content_type="application/json",
            headers={"accept": "text/plain"},
            REMOTE_ADDR=f"2001:db8::{uuid.uuid4().int & 0xFFFF:x}",
        )
        with override_settings(API_REQUEST_BODY_MAX_BYTES=1):
            too_large = client.post(
                "/api/v1/users/",
                data="oversized",
                content_type="text/plain",
                REMOTE_ADDR=f"2001:db8::{uuid.uuid4().int & 0xFFFF:x}",
            )
        unsupported = client.post(
            "/api/v1/users/",
            data="unsupported",
            content_type="text/plain",
            REMOTE_ADDR=f"2001:db8::{uuid.uuid4().int & 0xFFFF:x}",
        )
        activation_link = next(
            word for word in mail.outbox[-1].body.split() if word.startswith("http")
        )
        activation_query = parse_qs(urlparse(activation_link).query)
        activated = client.post(
            "/api/v1/users/",
            {
                "account": activation_query["account"][0],
                "token": activation_query["token"][0],
            },
            content_type="application/json",
        )

    throttle_address = f"2001:db8::{uuid.uuid4().int & 0xFFFF:x}"
    with override_settings(USER_REGISTRATION_ADDRESS_THROTTLE_RATE="1/minute"):
        client.post(
            "/api/v1/users/",
            _registration_payload(f"registration-limit-first-{uuid.uuid4().hex}"),
            content_type="application/json",
            REMOTE_ADDR=throttle_address,
        )
        throttled = client.post(
            "/api/v1/users/",
            _registration_payload(f"registration-limit-second-{uuid.uuid4().hex}"),
            content_type="application/json",
            REMOTE_ADDR=throttle_address,
        )

    with monkeypatch.context() as failure_patch:
        failure_patch.setattr(
            user_profiles_module,
            "register_account",
            lambda _data: (_ for _ in ()).throw(RuntimeError("induced")),
        )
        client.raise_request_exception = False
        with override_settings(USER_REGISTRATION_ADDRESS_THROTTLE_RATE=high_rate):
            unexpected = client.post(
                "/api/v1/users/",
                _registration_payload(f"registration-failure-{uuid.uuid4().hex}"),
                content_type="application/json",
                REMOTE_ADDR=f"2001:db8::{uuid.uuid4().int & 0xFFFF:x}",
            )

    def fail_admission(
        _store: PostgresLoginThrottleStore,
        _rules: object,
        *,
        member: str,
    ) -> None:
        """Raise the authoritative throttle outage documented by registration.

        Replaces only primary admission persistence while routing, middleware, correlation, and the
        public registration view remain real.

        Arguments:
            _store: Primary-backed throttle store receiving the request.
            _rules: Registration rolling-window rule.
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
            "/api/v1/users/",
            _registration_payload(f"registration-outage-{uuid.uuid4().hex}"),
            content_type="application/json",
            REMOTE_ADDR=f"2001:db8::{uuid.uuid4().int & 0xFFFF:x}",
        )

    observed = {
        response.status_code
        for response in (
            success,
            activated,
            invalid,
            method_not_allowed,
            not_acceptable,
            too_large,
            unsupported,
            throttled,
            unexpected,
            unavailable,
        )
    }
    generator = import_module("drf_spectacular.generators").SchemaGenerator()
    schema = cast("dict[str, Any]", generator.get_schema(request=None, public=True))
    documented = {
        int(status)
        for status in cast(
            "dict[str, Any]",
            cast("dict[str, Any]", schema["paths"])["/api/v1/users/"]["post"]["responses"],
        )
    }

    assert observed == documented
    assert cast("dict[str, Any]", unavailable.json())["code"] == ErrorCode.SERVICE_UNAVAILABLE


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@pytest.mark.parametrize("method", ["get", "patch", "delete"])
def test_profile_observed_statuses_exactly_match_each_documented_contract(
    client: Client,
    django_user_model: type[User],
    monkeypatch: pytest.MonkeyPatch,
    method: str,
) -> None:
    """Compare every reachable self-profile method status with OpenAPI.

    Drives each supported method through success, authentication, validation where applicable,
    routing, negotiation, size, representation, dependency, and unexpected-failure boundaries.

    Arguments:
        client: Django test client issuing the versioned requests.
        django_user_model: Configured custom user model class.
        monkeypatch: Fixture inducing documented dependency and unexpected failures.
        method: Supported profile method under test.

    Returns:
        None.

    Raises:
        AssertionError: If an observed profile status is missing from or extra in OpenAPI.
    """
    suffix = uuid.uuid4().hex
    account = django_user_model.objects.create_user(
        f"profile-contract-{method}-{suffix}",
        f"profile-contract-{method}-{suffix}@localforge.invalid",
        PASSWORD,
        is_active=True,
    )
    headers = _token_headers(account)
    valid_data: dict[str, str] | None = None
    invalid_data: dict[str, str] | None = None
    if method == "patch":
        valid_data = {"email": f"profile-contract-updated-{suffix}@localforge.invalid"}
        invalid_data = {"email": "not-an-address"}
    elif method == "delete":
        valid_data = {"current_password": PASSWORD}
        invalid_data = {}

    unauthorized = _invoke_profile_request(
        client,
        method,
        data=valid_data,
        content_type="application/json",
        headers=None,
    )
    method_not_allowed = cast(
        "ClientResponse",
        client.post("/api/v1/users/me/", headers=headers),
    )
    not_acceptable = _invoke_profile_request(
        client,
        method,
        data=valid_data,
        content_type="application/json",
        headers={**headers, "accept": "text/plain"},
    )
    with override_settings(API_REQUEST_BODY_MAX_BYTES=1):
        too_large = _invoke_profile_request(
            client,
            method,
            data="oversized",
            content_type="text/plain",
            headers=headers,
        )

    invalid = (
        _invoke_profile_request(
            client,
            method,
            data=invalid_data,
            content_type="application/json",
            headers=headers,
        )
        if invalid_data is not None
        else None
    )
    unsupported = (
        _invoke_profile_request(
            client,
            method,
            data="unsupported",
            content_type="text/plain",
            headers=headers,
        )
        if method != "get"
        else None
    )

    original_method = getattr(user_profiles_module.UserProfileView, method)

    def fail_unexpected(_view: object, _request: object) -> None:
        """Raise the unexpected failure documented by one profile operation.

        Replaces only the selected view method after authentication and framework admission,
        allowing the universal server-error boundary to render the public result.

        Arguments:
            _view: Profile view instance.
            _request: Authenticated REST request.

        Returns:
            Never returns.

        Raises:
            RuntimeError: Always, representing an unexpected profile failure.
        """
        message = "induced"
        raise RuntimeError(message)

    def fail_unavailable(_view: object, _request: object) -> None:
        """Raise the dependency failure documented by one profile operation.

        Replaces only the selected view method after authentication and framework admission,
        preserving the public route while exercising the correlated service-unavailable response.

        Arguments:
            _view: Profile view instance.
            _request: Authenticated REST request.

        Returns:
            Never returns.

        Raises:
            ServiceUnavailable: Always, representing authoritative account-state loss.
        """
        raise ServiceUnavailable

    with monkeypatch.context() as failure_patch:
        failure_patch.setattr(user_profiles_module.UserProfileView, method, fail_unexpected)
        client.raise_request_exception = False
        unexpected = _invoke_profile_request(
            client,
            method,
            data=valid_data,
            content_type="application/json",
            headers=headers,
        )

    with monkeypatch.context() as outage_patch:
        outage_patch.setattr(user_profiles_module.UserProfileView, method, fail_unavailable)
        unavailable = _invoke_profile_request(
            client,
            method,
            data=valid_data,
            content_type="application/json",
            headers=headers,
        )

    monkeypatch.setattr(user_profiles_module.UserProfileView, method, original_method)
    success = _invoke_profile_request(
        client,
        method,
        data=valid_data,
        content_type="application/json",
        headers=headers,
    )
    responses = [
        success,
        unauthorized,
        method_not_allowed,
        not_acceptable,
        too_large,
        unexpected,
        unavailable,
    ]
    if invalid is not None:
        responses.append(invalid)
    if unsupported is not None:
        responses.append(unsupported)

    observed = {response.status_code for response in responses}
    generator = import_module("drf_spectacular.generators").SchemaGenerator()
    schema = cast("dict[str, Any]", generator.get_schema(request=None, public=True))
    documented = {
        int(status)
        for status in cast(
            "dict[str, Any]",
            cast("dict[str, Any]", schema["paths"])["/api/v1/users/me/"][method]["responses"],
        )
    }

    assert observed == documented
