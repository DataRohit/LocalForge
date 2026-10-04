"""Unit tests for environment-controlled API documentation routes.

Exercises fresh Django processes at the URL-resolver seam so documentation exposure is proved for
both environments without relying on mutable module state from the test runner.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Final, cast

import pytest

from config.urls import documentation_urlpatterns

REPOSITORY_ROOT: Final = Path(__file__).resolve().parents[4]
OPENAPI_ARTIFACT: Final = REPOSITORY_ROOT / "docs" / "api" / "openapi-v1.yaml"
DOCUMENTATION_PATHS: Final = (
    "/api/schema/",
    "/api/schema/swagger-ui/",
    "/api/schema/redoc/",
)
FIXED_APPLICATION_PATHS: Final = [
    "/api/v1/jwt/create/",
    "/api/v1/jwt/refresh/",
    "/api/v1/jwt/verify/",
    "/api/v1/token/login/",
    "/api/v1/token/logout/",
    "/api/v1/users/",
    "/api/v1/users/me/",
    "/api/v1/users/resend_activation/",
    "/api/v1/users/reset_password/",
    "/api/v1/users/reset_password_confirm/",
    "/api/v1/users/reset_username/",
    "/api/v1/users/reset_username_confirm/",
    "/api/v1/users/set_password/",
    "/api/v1/users/set_username/",
    "/health/",
]


def _resolved_documentation_routes(*, enabled: bool) -> dict[str, str | None]:
    """Resolve documentation paths in one freshly configured Django process.

    Starts a separate settings import with the requested environment flag so URL construction is
    observed exactly as it happens at application startup rather than through a reloaded module.

    Arguments:
        enabled: Whether the documentation routes should be exposed.

    Returns:
        Each documentation path mapped to its resolved view name or ``None``.

    Raises:
        AssertionError: If the probe process cannot start or return JSON.
    """
    probe = """
import json
import django
from django.urls import Resolver404, resolve

django.setup()
paths = (
    "/api/schema/",
    "/api/schema/swagger-ui/",
    "/api/schema/redoc/",
)
resolved = {}
for path_value in paths:
    try:
        resolved[path_value] = resolve(path_value).view_name
    except Resolver404:
        resolved[path_value] = None
print(json.dumps(resolved))
"""
    environment = os.environ.copy()
    environment["DJANGO_SETTINGS_MODULE"] = "config.settings.testing"
    environment["DJANGO_API_DOCUMENTATION_ENABLED"] = str(enabled).lower()
    environment["PYTHONPATH"] = os.pathsep.join(
        (str(REPOSITORY_ROOT / "backend" / "src"), str(REPOSITORY_ROOT))
    )
    completed = subprocess.run(  # noqa: S603
        [sys.executable, "-c", probe],
        cwd=REPOSITORY_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr
    return cast("dict[str, str | None]", json.loads(completed.stdout))


def _documentation_responses() -> dict[str, Any]:
    """Request every documentation route in one enabled Django process.

    Uses the public test client and staticfiles finder to capture rendered markup, schema metadata,
    response security, and the local package asset paths the pages depend on.

    Arguments:
        None.

    Returns:
        JSON-compatible documentation response evidence.

    Raises:
        AssertionError: If the probe process cannot start or return JSON.
    """
    probe = """
import json
import re
import yaml
import django
from django.contrib.staticfiles import finders
from django.test import Client

django.setup()
client = Client()
request_headers = {
    "HTTP_HOST": "localhost",
    "HTTP_ORIGIN": "http://localhost:8080",
}
schema_response = client.get("/api/schema/", **request_headers)
swagger_response = client.get("/api/schema/swagger-ui/", **request_headers)
redoc_response = client.get("/api/schema/redoc/", **request_headers)
schema = yaml.safe_load(schema_response.content)
pages = {
    "swagger": swagger_response.content.decode(),
    "redoc": redoc_response.content.decode(),
}
assets = sorted({
    value
    for html in pages.values()
    for value in re.findall(r'(?:src|href)="([^"]+)"', html)
    if value.startswith("/static/")
})
print(json.dumps({
    "statuses": {
        "schema": schema_response.status_code,
        "swagger": swagger_response.status_code,
        "redoc": redoc_response.status_code,
    },
    "content_types": {
        "schema": schema_response.headers["Content-Type"],
        "swagger": swagger_response.headers["Content-Type"],
        "redoc": redoc_response.headers["Content-Type"],
    },
    "headers": {
        "swagger": dict(swagger_response.headers),
        "redoc": dict(redoc_response.headers),
    },
    "pages": pages,
    "assets": assets,
    "asset_files": {
        asset: finders.find(asset.removeprefix("/static/")) is not None
        for asset in assets
    },
    "openapi": schema["openapi"],
    "paths": sorted(schema["paths"]),
    "security_schemes": schema["components"]["securitySchemes"],
}))
"""
    environment = os.environ.copy()
    environment["DJANGO_SETTINGS_MODULE"] = "config.settings.testing"
    environment["DJANGO_API_DOCUMENTATION_ENABLED"] = "true"
    environment["PYTHONPATH"] = os.pathsep.join(
        (str(REPOSITORY_ROOT / "backend" / "src"), str(REPOSITORY_ROOT))
    )
    completed = subprocess.run(  # noqa: S603
        [sys.executable, "-c", probe],
        cwd=REPOSITORY_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr
    return cast("dict[str, Any]", json.loads(completed.stdout.splitlines()[-1]))


@pytest.mark.unit
def test_api_documentation_routes_follow_the_environment_flag() -> None:
    """Expose exactly three documentation routes only when explicitly enabled.

    Resolves the service-inventory paths in separate enabled and disabled processes, proving the
    flag changes the route table without a code change and testing remains headless by default.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If a route is missing, extra, renamed, or remains exposed when disabled.
    """
    assert _resolved_documentation_routes(enabled=True) == {
        "/api/schema/": "schema",
        "/api/schema/swagger-ui/": "swagger-ui",
        "/api/schema/redoc/": "redoc",
    }
    assert _resolved_documentation_routes(enabled=False) == dict.fromkeys(
        DOCUMENTATION_PATHS,
    )
    assert [pattern.name for pattern in documentation_urlpatterns(enabled=True)] == [
        "schema",
        "swagger-ui",
        "redoc",
    ]
    assert documentation_urlpatterns(enabled=False) == []


@pytest.mark.unit
def test_api_documentation_renders_from_only_local_assets() -> None:
    """Render the valid schema and both documentation pages without remote dependencies.

    Requests all three public routes, checks the fixed documented application surface, and proves
    every browser asset URL is local, installed, and compatible with the response security policy.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If a route fails, the schema drifts, or either page needs a remote asset.
    """
    evidence = _documentation_responses()
    pages = cast("dict[str, str]", evidence["pages"])
    headers = cast("dict[str, dict[str, str]]", evidence["headers"])
    assert evidence["statuses"] == {"schema": 200, "swagger": 200, "redoc": 200}
    assert evidence["content_types"] == {
        "schema": "application/vnd.oai.openapi; charset=utf-8",
        "swagger": "text/html; charset=utf-8",
        "redoc": "text/html; charset=utf-8",
    }
    assert evidence["openapi"] == "3.1.0"
    assert evidence["paths"] == FIXED_APPLICATION_PATHS
    assert evidence["security_schemes"] == {
        "cookieAuth": {
            "type": "apiKey",
            "in": "cookie",
            "name": "sessionid",
        },
        "jwtAuth": {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
        },
        "tokenAuth": {
            "type": "apiKey",
            "in": "header",
            "name": "Authorization",
            "description": "Send `Authorization: Token <key>`.",
        },
    }
    assert "SwaggerUIBundle({" in pages["swagger"]
    assert 'url: "/api/schema/"' in pages["swagger"]
    assert "/api/schema/" in pages["swagger"]
    assert "/api/schema/" in pages["redoc"]
    assert evidence["assets"] == [
        "/static/drf_spectacular_sidecar/redoc/bundles/redoc.standalone.js",
        "/static/drf_spectacular_sidecar/swagger-ui-dist/favicon-32x32.png",
        "/static/drf_spectacular_sidecar/swagger-ui-dist/swagger-ui-bundle.js",
        "/static/drf_spectacular_sidecar/swagger-ui-dist/swagger-ui-standalone-preset.js",
        "/static/drf_spectacular_sidecar/swagger-ui-dist/swagger-ui.css",
    ]
    assert all(cast("dict[str, bool]", evidence["asset_files"]).values())
    assert all(
        remote_reference not in html
        for html in pages.values()
        for remote_reference in ("http://", "https://", 'src="//', 'href="//')
    )
    for response_headers in headers.values():
        assert response_headers["Content-Security-Policy"] == (
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
            "script-src 'self' 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'; "
            "form-action 'self'"
        )
        assert response_headers["X-Content-Type-Options"] == "nosniff"
        assert response_headers["X-Frame-Options"] == "DENY"
        assert response_headers["Referrer-Policy"] == "same-origin"
        assert response_headers["Access-Control-Allow-Origin"] == "http://localhost:8080"
        assert response_headers["Access-Control-Allow-Credentials"] == "true"


@pytest.mark.unit
def test_direct_schema_generation_excludes_enabled_documentation_infrastructure() -> None:
    """Generate the fixed contract directly while documentation routes are enabled.

    Runs both ``SchemaGenerator`` and the warning-failing management command in fresh processes,
    proving optional infrastructure paths neither crash generation nor alter the artifact.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If direct generation fails, warns, adds paths, or changes output.
    """
    environment = os.environ.copy()
    environment["DJANGO_SETTINGS_MODULE"] = "config.settings.testing"
    environment["DJANGO_API_DOCUMENTATION_ENABLED"] = "true"
    environment["PYTHONPATH"] = os.pathsep.join(
        (str(REPOSITORY_ROOT / "backend" / "src"), str(REPOSITORY_ROOT))
    )
    probe = """
import json
import django

django.setup()
from drf_spectacular.generators import SchemaGenerator

schema = SchemaGenerator().get_schema(request=None, public=True)
print(json.dumps(sorted(schema["paths"])))
"""
    direct = subprocess.run(  # noqa: S603
        [sys.executable, "-c", probe],
        cwd=REPOSITORY_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        timeout=30,
    )

    assert direct.returncode == 0, direct.stderr.decode()
    assert direct.stderr == b""
    assert json.loads(direct.stdout) == FIXED_APPLICATION_PATHS

    managed = subprocess.run(  # noqa: S603
        [
            sys.executable,
            str(REPOSITORY_ROOT / "backend" / "src" / "manage.py"),
            "spectacular",
            "--validate",
            "--fail-on-warn",
        ],
        cwd=REPOSITORY_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        timeout=30,
    )

    assert managed.returncode == 0, managed.stderr.decode()
    assert managed.stderr == b""
    assert managed.stdout.replace(b"\r\n", b"\n") == OPENAPI_ARTIFACT.read_bytes()
