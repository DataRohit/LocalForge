"""Unit tests for OpenAPI post-processing policy.

Exercises schema-hook ownership and defensive incomplete-document handling
without generating the complete published contract.
"""

import json
from typing import Any

import pytest
from django.conf import settings

from config.openapi import add_throttle_response_headers


@pytest.mark.unit
def test_openapi_hooks_are_owned_by_the_schema_module() -> None:
    """Keep schema generation independent from the runtime API module.

    Requires both post-processing hooks to resolve through the OpenAPI module, so documentation
    policy can change without editing the runtime middleware and exception interface.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If settings couple schema policy back to the runtime API module.
    """
    assert settings.SPECTACULAR_SETTINGS["POSTPROCESSING_HOOKS"] == [
        "config.openapi.add_throttle_response_headers",
        "config.openapi.finalize_openapi_contract",
    ]


@pytest.mark.unit
@pytest.mark.parametrize(
    "document",
    [
        {"paths": []},
        {"paths": {"/probe/": []}},
        {"paths": {"/probe/": {"get": []}}},
        {"paths": {"/probe/": {"get": {"responses": []}}}},
    ],
)
def test_throttle_header_hook_ignores_incomplete_schema_shapes(
    document: dict[str, Any],
) -> None:
    """Leave incomplete schema structures unchanged.

    Supplies each defensive non-mapping shape accepted by the post-processing hook and verifies it
    returns the same document rather than failing schema generation.

    Arguments:
        document: Incomplete generated schema shape under test.

    Returns:
        None.

    Raises:
        AssertionError: If a defensive branch mutates or rejects the document.
    """
    before = json.dumps(document, sort_keys=True)

    observed = add_throttle_response_headers(document, object(), object(), object())

    assert observed is document
    assert json.dumps(observed, sort_keys=True) == before
