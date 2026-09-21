"""Unit tests for the WSGI entry point.

Verifies compatibility tooling receives a real Django WSGI handler without starting a server.
The deployed ASGI stack remains unaffected by this compatibility module.
"""

import pytest
from django.core.handlers.wsgi import WSGIHandler

from config.wsgi import application


@pytest.mark.unit
def test_wsgi_entry_point_exposes_the_framework_handler() -> None:
    """Expose the handler type expected by WSGI tooling.

    Checks the public module object directly without opening a listener.
    Tooling must receive Django's maintained handler type.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the WSGI entry point changes type.
    """
    assert isinstance(application, WSGIHandler)
