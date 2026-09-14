"""Root URL configuration.

Maps the project's URL prefixes onto the views that serve them, holding the administration route
and the health endpoint the platform polls until the fixed application surface is built on top.
"""

from django.contrib import admin
from django.urls import path
from health_check.views import HealthCheckView

urlpatterns = [
    path("admin/", admin.site.urls),
    path(
        "health/",
        HealthCheckView.as_view(checks=["health_check.checks.Database"]),
        name="health",
    ),
]
