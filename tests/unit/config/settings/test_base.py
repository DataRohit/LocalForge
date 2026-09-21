"""Unit tests for shared settings policy.

Verifies the configured security, password, queue, and storage invariants exposed by the shared
module after startup validation succeeds.
"""

import pytest

from config.settings import base

CELERY_QUEUE_COUNT = 3


@pytest.mark.unit
def test_shared_settings_publish_the_required_security_invariants() -> None:
    """Keep security and infrastructure identities deliberately separated.

    Reads only public configured values and verifies the password hasher, queue names, cache
    databases, and browser policy retain their fixed safe shape.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If any validated settings invariant drifts.
    """
    assert base.PASSWORD_HASHERS[0] == "django.contrib.auth.hashers.Argon2PasswordHasher"
    assert (
        len({base.CELERY_DEFAULT_QUEUE, base.CELERY_SLOW_QUEUE, base.CELERY_DEAD_LETTER_QUEUE})
        == CELERY_QUEUE_COUNT
    )
    cache_settings = base.CACHES["default"]
    assert isinstance(cache_settings, dict)
    cache_location = cache_settings["LOCATION"]

    assert str(cache_location).endswith("/0")
    assert str(base.CELERY_RESULT_BACKEND).endswith("/1")
    assert base.X_FRAME_OPTIONS == "DENY"
    assert "frame-ancestors 'none'" in base.CONTENT_SECURITY_POLICY
