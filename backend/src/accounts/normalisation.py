"""Canonical form for account identifiers.

Holds the one definition of what an address looks like once stored, so the manager, the model, and
every later serializer agree rather than each trimming and lowercasing in its own way.
"""


def normalise_email(email: str | None) -> str:
    """Reduce an address to the form the database stores.

    Trims surrounding whitespace and lowercases the whole address, not only its domain, so a lookup
    by address is an exact match and cannot silently miss an account that signed up in another case.

    Arguments:
        email: Address as the caller supplied it, or None.

    Returns:
        The canonical form, or an empty string when nothing was supplied.

    Raises:
        None.
    """
    return (email or "").strip().lower()
