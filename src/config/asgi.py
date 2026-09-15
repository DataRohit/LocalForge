"""ASGI entry point for the project.

Exposes the application object an ASGI server serves, dispatching HTTP to Django and WebSocket
connections to the Channels router, and defaulting to the development settings.
"""

import os
from typing import TYPE_CHECKING, cast

from channels.routing import ProtocolTypeRouter, URLRouter
from django.core.asgi import get_asgi_application

from config.logs import finalize_streaming_asgi

if TYPE_CHECKING:
    from asgiref.typing import ASGI3Application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")

django_application = get_asgi_application()
http_application = finalize_streaming_asgi(
    cast("ASGI3Application", django_application),
)

from config.routing import websocket_urlpatterns  # noqa: E402

application = ProtocolTypeRouter(
    {
        "http": http_application,
        "websocket": URLRouter(websocket_urlpatterns),
    }
)
