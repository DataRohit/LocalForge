"""Unit tests for the internal metrics URL table.

Keeps the observability listener restricted to django-prometheus routes instead of the application
surface.
"""

import pytest

from config.metrics_urls import urlpatterns


@pytest.mark.unit
def test_metrics_url_table_contains_only_the_prometheus_include() -> None:
    """Expose one root include and no application route.

    Inspects the complete URL table without dispatching a request or touching a service.
    Application routes must remain absent from this listener.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If another route enters the metrics listener.
    """
    assert len(urlpatterns) == 1
    assert str(urlpatterns[0].pattern) == ""
