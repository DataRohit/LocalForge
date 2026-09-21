"""Unit tests for the observability-only ASGI entry point.

Checks the metrics listener selects the internal URL table and exposes an ASGI callable without
serving a network request.
"""

import importlib
import sys

import pytest
from django.conf import settings


@pytest.mark.unit
def test_metrics_entry_point_uses_only_the_internal_url_table() -> None:
    """Select the metrics URL configuration for the isolated listener.

    Imports the entry point under a saved setting and restores global test configuration after
    observing the module's public application.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the metrics entry point selects another URL table.
    """
    original = settings.ROOT_URLCONF
    sys.modules.pop("config.metrics_asgi", None)
    try:
        module = importlib.import_module("config.metrics_asgi")

        assert callable(module.application)
        assert settings.ROOT_URLCONF == "config.metrics_urls"
    finally:
        settings.ROOT_URLCONF = original
        sys.modules.pop("config.metrics_asgi", None)
