"""Unit tests for the Phase 11 Freeze Manifest.

Keeps the published manifest and capability matrix tied to the code: every cited test must exist,
every browser route in the generated schema must be listed, and no frontend capability may be left
unmapped.
"""

import re
import subprocess
import sys
from io import StringIO
from pathlib import Path
from typing import Final

import pytest
import yaml
from django.core.management import call_command

_REPOSITORY_ROOT_CANDIDATE = Path(__file__).resolve().parents[3]
REPOSITORY_ROOT: Final = (
    _REPOSITORY_ROOT_CANDIDATE
    if (_REPOSITORY_ROOT_CANDIDATE / "docs").is_dir()
    else Path(__file__).resolve().parents[2]
)
MANIFEST: Final = REPOSITORY_ROOT / "docs" / "handover" / "phase-11.md"
NODE_PATTERN: Final = re.compile(r"backend/tests/[\w/]+\.py::[\w\-\[\]]+")
COLLECTION_TIMEOUT_SECONDS: Final = 120
BROWSER_OPERATION_COUNT: Final = 5
REQUIRED_CAPABILITIES: Final = (
    "Registration",
    "Activation",
    "Resend activation",
    "Sign-in",
    "Session upkeep",
    "Sign-out",
    "Capability-guarded navigation",
    "Profile read",
    "Profile update",
    "Account deletion",
    "Password change",
    "Password recovery",
    "Username change",
    "Username recovery",
    "Live notifications",
)
REQUIRED_CLOSE_CODES: Final = ("4402", "4403", "4404", "4406")
REQUIRED_COOKIES: Final = ("__Host-lf-access", "__Secure-lf-refresh", "__Host-lf-csrf")


def _manifest_text() -> str:
    """Read the published manifest.

    Reads the committed handover document as UTF-8 text.
    Fails the test with a clear error when it is absent.

    Arguments:
        None.

    Returns:
        The manifest text.
    """
    return MANIFEST.read_text(encoding="utf-8")


@pytest.mark.unit
def test_every_test_cited_by_the_manifest_collects() -> None:
    """Collect every full node identifier the manifest cites.

    Runs pytest collection on the exact identifiers, so a renamed or removed proving test fails
    before the manifest can claim it as evidence.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If no identifier is cited or any cited test cannot be collected.
        subprocess.TimeoutExpired: If collection exceeds its bounded duration.
    """
    references = sorted(set(NODE_PATTERN.findall(_manifest_text())))
    assert len(references) >= len(REQUIRED_CAPABILITIES)

    result = subprocess.run(  # noqa: S603
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "--no-cov",
            "-q",
            *(reference.removeprefix("backend/") for reference in references),
        ],
        cwd=REPOSITORY_ROOT / "backend"
        if (REPOSITORY_ROOT / "backend").is_dir()
        else REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=COLLECTION_TIMEOUT_SECONDS,
    )

    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.unit
def test_the_manifest_lists_every_browser_route_in_the_schema() -> None:
    """List every generated browser operation in the manifest.

    Generates the schema and requires each operation under the browser prefix, with its method, to
    appear in the route table, so a new route cannot ship unfrozen.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If a generated browser operation is missing from the manifest.
    """
    output = StringIO()
    call_command("spectacular", format="openapi", stdout=output, verbosity=0)
    schema = yaml.safe_load(output.getvalue())
    text = _manifest_text()
    operations = [
        (method.upper(), route.removeprefix("/api/v1/browser/"))
        for route, item in schema["paths"].items()
        if route.startswith("/api/v1/browser/")
        for method in item
    ]

    assert len(operations) == BROWSER_OPERATION_COUNT
    for method, route in operations:
        assert f"`{method} {route}`" in text


@pytest.mark.unit
def test_the_manifest_maps_every_capability_cookie_and_close_code() -> None:
    """Require the matrix, cookies, and close codes to be present.

    Checks the capability rows, the three cookie names, and the WebSocket close codes the cookie
    path relies on, so none can be dropped silently.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If a capability row, cookie, or close code is absent.
    """
    text = _manifest_text()

    for capability in REQUIRED_CAPABILITIES:
        assert f"| {capability} |" in text
    for cookie in REQUIRED_COOKIES:
        assert f"`{cookie}`" in text
    for code in REQUIRED_CLOSE_CODES:
        assert f"| {code} |" in text
