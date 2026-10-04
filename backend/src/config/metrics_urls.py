"""Internal metrics URL configuration.

Exposes only django-prometheus collection routes to the observability listener, keeping operational
metrics out of the application URL table served on the edge-facing port.
"""

from django.urls import include, path

urlpatterns = [
    path("", include("django_prometheus.urls")),
]
