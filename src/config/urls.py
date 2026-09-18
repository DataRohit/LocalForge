"""Root URL configuration.

Maps the fixed application and infrastructure routes, keeping the optional local documentation
surface separate so its environment flag never changes the OpenAPI document it renders.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.conf import settings
from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)

from config.api import (
    API_PREFIX,
    api_bad_request,
    api_not_found,
    api_permission_denied,
    api_server_error,
)
from config.health import ReadinessView

if TYPE_CHECKING:
    from django.urls import URLPattern

application_urlpatterns = [
    path("admin/", admin.site.urls),
    path(API_PREFIX, include("config.api", namespace="api-v1")),
    path(
        "health/",
        ReadinessView.as_view(),
        name="health",
    ),
]


def documentation_urlpatterns(*, enabled: bool) -> list[URLPattern]:
    """Build the optional schema and documentation route table.

    Returns no routes for a headless process and otherwise binds the schema generator to only the
    fixed application patterns, preventing the documentation routes from documenting themselves.

    Arguments:
        enabled: Whether this process exposes API documentation.

    Returns:
        The schema, Swagger UI, and ReDoc patterns, or an empty list.
    """
    if not enabled:
        return []

    return [
        path(
            "api/schema/",
            SpectacularAPIView.as_view(patterns=application_urlpatterns),
            name="schema",
        ),
        path(
            "api/schema/swagger-ui/",
            SpectacularSwaggerView.as_view(url_name="schema"),
            name="swagger-ui",
        ),
        path(
            "api/schema/redoc/",
            SpectacularRedocView.as_view(url_name="schema"),
            name="redoc",
        ),
    ]


urlpatterns = [
    *application_urlpatterns,
    *documentation_urlpatterns(enabled=settings.API_DOCUMENTATION_ENABLED),
]

handler400 = api_bad_request
handler403 = api_permission_denied
handler404 = api_not_found
handler500 = api_server_error
