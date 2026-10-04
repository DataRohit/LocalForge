"""Unit tests for authoritative REST authentication adapters.

Exercises malformed identity classification and the two public OpenAPI scheme definitions without
reading token or account state.
"""

from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest
from rest_framework.exceptions import AuthenticationFailed

from accounts.authentication import (
    JWTAuthenticationScheme,
    PrimaryTokenAuthenticationScheme,
    active_token_user,
)

if TYPE_CHECKING:
    from rest_framework_simplejwt.tokens import Token


@pytest.mark.unit
@pytest.mark.parametrize("identity", [None, 7, "not-a-uuid"])
def test_active_token_user_rejects_malformed_identity_before_database_lookup(
    identity: object,
) -> None:
    """Reject identity claims that cannot name a project account.

    Supplies a token-shaped payload and requires generic authentication failure before the ORM
    connection guard could be reached. No account query is permitted.

    Arguments:
        identity: Invalid identity claim.

    Returns:
        None.

    Raises:
        AuthenticationFailed: Expected for every malformed claim.
    """
    token = cast("Token", SimpleNamespace(payload={"user_id": identity}))

    with pytest.raises(AuthenticationFailed):
        active_token_user(token)


@pytest.mark.unit
def test_authentication_extensions_publish_the_runtime_header_syntax() -> None:
    """Describe both accepted authorization schemes exactly.

    Calls the schema adapters directly and compares independent complete definitions.
    The expected values come from the documented runtime header contract.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If either header contract drifts.
    """
    assert JWTAuthenticationScheme(object()).get_security_definition(object()) == {  # type: ignore[no-untyped-call]
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "JWT",
    }
    assert PrimaryTokenAuthenticationScheme(object()).get_security_definition(object()) == {  # type: ignore[no-untyped-call]
        "type": "apiKey",
        "in": "header",
        "name": "Authorization",
        "description": "Send `Authorization: Token <key>`.",
    }
