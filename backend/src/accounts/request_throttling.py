"""Request identity and configured throttle-rate parsing.

Provides the shared client-address trust boundary and count-per-period parser used by both
PostgreSQL security admission and general cache-backed API scopes.
"""

from ipaddress import ip_address
from typing import TYPE_CHECKING, cast

from django.conf import settings

if TYPE_CHECKING:
    from collections.abc import Mapping

    from asgiref.typing import Scope
    from django.http import HttpRequest
    from rest_framework.request import Request


def parse_throttle_rate(rate: str) -> tuple[int, int]:
    """Parse one configured count-per-period rate.

    Accepts the repository's ``count/period`` format and resolves the period by its first letter,
    matching the documented second, minute, hour, and day vocabulary.

    Arguments:
        rate: Environment-derived count and period.

    Returns:
        Positive request limit and window duration in seconds.

    Raises:
        ValueError: If the configured rate is malformed or non-positive.
    """
    try:
        count_text, period = rate.split("/", maxsplit=1)
        count = int(count_text)
        duration = {"s": 1, "m": 60, "h": 3600, "d": 86400}[period[0].lower()]
    except (IndexError, KeyError, ValueError) as error:
        message = f"invalid strict throttle rate: {rate!r}"
        raise ValueError(message) from error

    if count <= 0:
        message = f"strict throttle count must be positive: {rate!r}"
        raise ValueError(message)

    return count, duration


def _trusted_client_address(remote_text: str, forwarded: str | None) -> str:
    """Resolve one address from peer and forwarded transport values.

    Trusts forwarding metadata only when the immediate peer belongs to a configured network and
    walks that chain right-to-left past every trusted hop.

    Arguments:
        remote_text: Immediate transport peer text.
        forwarded: Combined forwarded-address chain, or ``None`` when absent.

    Returns:
        Canonical client address used for throttle buckets.
    """
    try:
        remote_address = ip_address(remote_text)
    except ValueError:
        return remote_text

    trusted_networks = settings.TRUSTED_PROXY_NETWORKS
    if not any(remote_address in network for network in trusted_networks):
        return remote_address.compressed

    if not isinstance(forwarded, str) or not forwarded.strip():
        return remote_address.compressed

    for value in reversed(forwarded.split(",")):
        try:
            forwarded_address = ip_address(value.strip())
        except ValueError:
            return remote_address.compressed
        if not any(forwarded_address in network for network in trusted_networks):
            return forwarded_address.compressed

    return remote_address.compressed


def trusted_client_address(request: HttpRequest | Request) -> str:
    """Resolve one Django request address through explicitly trusted proxy hops.

    Reads the immediate WSGI or ASGI peer metadata and delegates the shared trust algorithm used by
    the outer ASGI boundary and post-identity REST framework scopes.

    Arguments:
        request: REST request carrying peer and optional forwarded metadata.

    Returns:
        Canonical client address used for throttle buckets.
    """
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")

    return _trusted_client_address(
        str(request.META.get("REMOTE_ADDR", "")),
        forwarded if isinstance(forwarded, str) else None,
    )


def trusted_client_address_from_scope(scope: Scope | Mapping[str, object]) -> str:
    """Resolve one ASGI scope address through the shared proxy trust boundary.

    Reads the immediate ASGI client tuple and combines repeated forwarded headers in transport
    order before applying the same canonicalization and trusted-hop walk as Django requests.

    Arguments:
        scope: Incoming ASGI connection scope.

    Returns:
        Canonical client address used for throttle buckets.
    """
    client = scope.get("client")
    remote_text = (
        str(client[0])
        if isinstance(client, tuple) and client and isinstance(client[0], str)
        else ""
    )
    headers = cast("list[tuple[bytes, bytes]]", scope.get("headers", []))
    forwarded_values = [
        value.decode("latin-1") for name, value in headers if name.lower() == b"x-forwarded-for"
    ]

    return _trusted_client_address(
        remote_text,
        ",".join(forwarded_values) if forwarded_values else None,
    )
