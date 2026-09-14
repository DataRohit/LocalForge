"""WebSocket routing for the project.

Holds the URL patterns the WebSocket protocol dispatches on, empty until the notification socket is
built, so the routing table has one home rather than growing inside the entry point.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from django.urls import URLPattern, URLResolver

websocket_urlpatterns: list[URLPattern | URLResolver] = []
