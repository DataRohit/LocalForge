"""Unit tests for development-only settings.

Verifies the environment adds browsable rendering and query logging while inheriting the shared
platform policy.
"""

from typing import cast

import pytest

from config.settings import development


@pytest.mark.unit
def test_development_settings_add_local_diagnostics() -> None:
    """Enable the browsable renderer and structured database query logger.

    Checks the complete development-only additions without re-executing environment loading.
    Query records remain non-propagating structured output.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If local diagnostics disappear or propagate unexpectedly.
    """
    renderers = cast("list[str]", development.REST_FRAMEWORK["DEFAULT_RENDERER_CLASSES"])
    logger = cast("dict[str, object]", development.LOGGING["loggers"]["django.db.backends"])

    assert "rest_framework.renderers.BrowsableAPIRenderer" in renderers
    assert logger["handlers"] == ["queries"]
    assert logger["propagate"] is False
