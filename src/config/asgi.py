"""ASGI entry point for the project.

Exposes the application object an ASGI server serves, dispatching HTTP to Django and WebSocket
connections to the Channels router, and defaulting to the development settings.
"""

import os
from importlib import import_module
from typing import TYPE_CHECKING, cast

from channels.routing import ProtocolTypeRouter, URLRouter
from django.core.asgi import get_asgi_application

if TYPE_CHECKING:
    from asgiref.typing import ASGI3Application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")

django_application = get_asgi_application()
api_module = import_module("config.api")
logs_module = import_module("config.logs")
routing_module = import_module("config.routing")
http_application = logs_module.finalize_streaming_asgi(
    api_module.api_boundary_throttle_asgi(
        api_module.api_request_body_limit_asgi(cast("ASGI3Application", django_application)),
    ),
)

application = ProtocolTypeRouter(
    {
        "http": http_application,
        "websocket": URLRouter(routing_module.websocket_urlpatterns),
    }
)
