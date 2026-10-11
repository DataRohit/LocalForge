"""Support for the CSRF rejection that status-contract evidence tests must observe.

Builds one cookie-authenticated unsafe request carrying a wrong CSRF token, the situation in
which every such route answers with the enveloped permission-denied status.
"""

import json
from typing import TYPE_CHECKING, cast

from tests.integration.accounts.test_browser_sessions import _address, _signed_in

if TYPE_CHECKING:
    from django.http import HttpResponse

    from accounts.models import User

FORGED_CSRF_TOKEN = "x" * 64


def csrf_rejected_response(
    user_model: type[User],
    method: str,
    route: str,
    body: dict[str, str],
) -> HttpResponse:
    """Send a cookie-authenticated unsafe request with a wrong CSRF token.

    Signs a fresh account in through the browser route so the access cookie is genuine, then
    replaces the CSRF header with a value that cannot match the cookie.

    Arguments:
        user_model: Configured custom user model class.
        method: HTTP method of the unsafe operation.
        route: Versioned route under test.
        body: JSON body the operation would accept.

    Returns:
        The rejected response.
    """
    client, headers, _account = _signed_in(user_model)

    return cast(
        "HttpResponse",
        client.generic(
            method.upper(),
            route,
            data=json.dumps(body),
            content_type="application/json",
            REMOTE_ADDR=_address(),
            headers={**headers, "X-CSRFToken": FORGED_CSRF_TOKEN},
        ),
    )
