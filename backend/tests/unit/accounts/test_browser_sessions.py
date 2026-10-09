"""Unit tests for the browser session helpers.

Exercises refresh-cookie parsing and the revocation helper's rejection and outage paths without
touching the database.
"""

import uuid
from unittest.mock import patch

import pytest
from django.conf import settings
from django.db import DatabaseError
from django.test import RequestFactory
from rest_framework.parsers import JSONParser
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

import accounts.browser_sessions as sessions
from config.api_errors import ServiceUnavailable


class FakeRefreshToken(dict[str, str]):
    """Refresh token stand-in exposing a payload mapping.

    Inherits from ``dict`` for claim lookup and carries the payload the revocation helper reads.
    Its identity claim is deliberately not a UUID.

    Attributes:
        payload: Claim mapping with an unusable identity.

    Members:
        None beyond those inherited from ``dict``.
    """

    payload = {"user_id": "not-a-uuid"}  # noqa: RUF012


@pytest.mark.unit
@pytest.mark.parametrize(("value", "expected"), [(None, None), ("", None), ("token", "token")])
def test_the_refresh_cookie_credential_treats_empty_as_absent(
    value: str | None,
    expected: str | None,
) -> None:
    """Read the refresh credential, collapsing empty to absent.

    A cleared cookie and a missing cookie must behave identically.
    Covers absent, empty, and populated values.

    Arguments:
        value: Cookie value, or None for no cookie.
        expected: Expected credential.

    Returns:
        None.

    Raises:
        AssertionError: If the credential differs.
    """
    request = Request(RequestFactory().post("/api/v1/browser/refresh/"))
    if value is not None:
        request.COOKIES[settings.BROWSER_REFRESH_COOKIE_NAME] = value

    assert sessions.refresh_cookie_credential(request) == expected


@pytest.mark.unit
def test_revoking_a_malformed_token_is_a_harmless_no_op() -> None:
    """Treat an unusable refresh token as an already ended session.

    Passes a value that cannot parse as a token and expects no revocation and no error.
    This keeps logout repeatable.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If a revocation is reported.
    """
    assert sessions.revoke_refresh_session("not-a-token") is None


@pytest.mark.unit
def test_a_blacklist_write_outage_is_service_unavailable() -> None:
    """Report an authoritative-store failure during revocation.

    Replaces the token parser and the transaction so the write fails, and expects the shared
    service-unavailable exception.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the outage is swallowed.
    """
    with (
        patch.object(sessions, "PrimaryRefreshToken", return_value=FakeRefreshToken(jti="x")),
        patch("accounts.browser_sessions.transaction.atomic", side_effect=DatabaseError),
        pytest.raises(ServiceUnavailable),
    ):
        sessions.revoke_refresh_session("token")


@pytest.mark.unit
def test_a_token_without_a_valid_identity_revokes_but_names_no_account() -> None:
    """Return no account identifier when the identity claim is unusable.

    Replaces the parser and the database access so only the identity handling runs.
    No identifier may be invented.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If an identifier is invented.
    """
    with (
        patch.object(sessions, "PrimaryRefreshToken", return_value=FakeRefreshToken(jti="x")),
        patch("accounts.browser_sessions.transaction.atomic"),
        patch("accounts.browser_sessions.OutstandingToken.objects"),
        patch("accounts.browser_sessions.BlacklistedToken.objects"),
    ):
        assert sessions.revoke_refresh_session("token") is None


@pytest.mark.unit
def test_a_live_token_without_an_outstanding_row_still_reports_its_account() -> None:
    """Report the account even when no outstanding record exists to blacklist.

    Replaces the token parser and the database managers so the revocation helper finds no
    outstanding row, writes no blacklist entry, and still returns the owning account.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If a blacklist row is written or the account is not named.
    """
    account = uuid.UUID(int=11)
    token = FakeRefreshToken(jti="x")
    token.payload = {"user_id": str(account)}
    outstanding = patch("accounts.browser_sessions.OutstandingToken.objects")
    blacklist = patch("accounts.browser_sessions.BlacklistedToken.objects")

    with (
        patch.object(sessions, "PrimaryRefreshToken", return_value=token),
        patch("accounts.browser_sessions.transaction.atomic"),
        outstanding as outstanding_manager,
        blacklist as blacklist_manager,
    ):
        lookup = outstanding_manager.using.return_value.select_for_update.return_value
        lookup.filter.return_value.first.return_value = None

        assert sessions.revoke_refresh_session("token") == account

    blacklist_manager.using.assert_not_called()


@pytest.mark.unit
def test_login_reports_a_primary_outage_as_service_unavailable() -> None:
    """Convert a database failure during sign-in into the shared outage exception.

    Calls the view body directly with a valid body and a failing credential verifier, so the
    conversion is proven without a database.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the outage escapes as a database error.
    """
    django_request = APIRequestFactory().post(
        "/api/v1/browser/login/",
        {"username": "river", "password": "secret"},
        format="json",
    )
    request = Request(django_request, parsers=[JSONParser()])

    with (
        patch("accounts.credentials.verify_login_credentials", side_effect=DatabaseError),
        pytest.raises(ServiceUnavailable),
    ):
        sessions.BrowserLoginView().post(request)
