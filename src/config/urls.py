"""Root URL configuration.

Maps the project's URL prefixes onto the views that serve them, holding the administration route
and the health endpoint the platform polls until the fixed application surface is built on top.
"""

from django.contrib import admin
from django.urls import path

from config.health import ReadinessView

urlpatterns = [
    path("admin/", admin.site.urls),
    path(
        "health/",
        ReadinessView.as_view(),
        name="health",
    ),
]
