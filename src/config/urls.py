"""Root URL configuration.

Maps the project's URL prefixes onto the views that serve them, holding the administration route
and the health endpoint the platform polls until the fixed application surface is built on top.
"""

from django.contrib import admin
from django.urls import path

from config.health import CorrelatedHealthCheckView

urlpatterns = [
    path("admin/", admin.site.urls),
    path(
        "health/",
        CorrelatedHealthCheckView.as_view(checks=["config.health.TimedDatabaseHealthCheck"]),
        name="health",
    ),
]
