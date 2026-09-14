"""Root URL configuration.

Maps the project's URL prefixes onto the views that serve them, holding only the administration
route until the fixed application surface is built on top of it.
"""

from django.contrib import admin
from django.urls import path

urlpatterns = [
    path("admin/", admin.site.urls),
]
