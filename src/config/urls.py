"""Root URL configuration.

Maps the project's URL prefixes onto the views that serve them, holding the administration route
and the health endpoint the platform polls until the fixed application surface is built on top.
"""

from django.contrib import admin
from django.urls import include, path

from config.api import (
    API_PREFIX,
    api_bad_request,
    api_not_found,
    api_permission_denied,
    api_server_error,
)
from config.health import ReadinessView

urlpatterns = [
    path("admin/", admin.site.urls),
    path(API_PREFIX, include("config.api", namespace="api-v1")),
    path(
        "health/",
        ReadinessView.as_view(),
        name="health",
    ),
]

handler400 = api_bad_request
handler403 = api_permission_denied
handler404 = api_not_found
handler500 = api_server_error
