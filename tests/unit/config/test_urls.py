"""Unit tests for the root URL configuration.

Covers the routes the project exposes, confirming each one resolves to the expected view so a
change to the URL table is deliberate rather than accidental.
"""

import importlib
import sys

import pytest
from django.conf import settings
from django.urls import Resolver404, resolve


@pytest.mark.unit
def test_admin_route_resolves() -> None:
    """Resolve the administration route.

    Confirms the admin is mounted at its expected prefix, which is the only route the project
    exposes before the application surface is built.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the admin route does not resolve to its expected pattern.
    """
    assert resolve("/admin/").route == "admin/"


@pytest.mark.unit
def test_metrics_route_resolves_to_the_instrumentation_exporter() -> None:
    """Resolve the internal metrics endpoint.

    Confirms the exporter is mounted at the path Prometheus scrapes, rather than merely installing
    instrumentation that has no reachable collection endpoint.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the metrics route is absent or moved.
    """
    match = resolve("/metrics", urlconf="config.metrics_urls")

    assert match.route == "metrics"
    assert match.url_name == "prometheus-django-metrics"

    with pytest.raises(Resolver404):
        resolve("/metrics")


@pytest.mark.unit
def test_metrics_asgi_application_uses_only_the_internal_url_table() -> None:
    """Build the ASGI handler for the isolated metrics listener.

    Imports the listener module with a controlled URL setting and restores the test process
    afterward, proving its application is callable and selects the metrics-only routes.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the listener does not build or selects another URL table.
    """
    original_urlconf = settings.ROOT_URLCONF
    sys.modules.pop("config.metrics_asgi", None)

    try:
        module = importlib.import_module("config.metrics_asgi")

        assert callable(module.application)
        assert settings.ROOT_URLCONF == "config.metrics_urls"
    finally:
        settings.ROOT_URLCONF = original_urlconf
        sys.modules.pop("config.metrics_asgi", None)
