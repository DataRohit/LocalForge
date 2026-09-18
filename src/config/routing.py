"""WebSocket routing for the project.

Holds the URL patterns the WebSocket protocol dispatches on, empty until the notification socket is
built, so the routing table has one home rather than growing inside the entry point.
"""

from channels.routing import URLRouter
from django.urls import path

from notifications.websocket import ExactWebSocketOriginValidator, NotificationConsumer

websocket_urlpatterns = [
    path("ws/notifications/", NotificationConsumer.as_asgi(), name="notification-websocket"),
]
websocket_application = ExactWebSocketOriginValidator(URLRouter(websocket_urlpatterns))
