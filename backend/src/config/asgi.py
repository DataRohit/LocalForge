"""ASGI entry point for the project.

Exposes the application object an ASGI server serves, dispatching HTTP to Django and WebSocket
connections to the Channels router, and serving documentation assets only when their routes exist.
"""

from __future__ import annotations

import os
from importlib import import_module
from typing import TYPE_CHECKING, cast, override

from channels.routing import ProtocolTypeRouter
from django.conf import settings
from django.contrib.staticfiles.handlers import ASGIStaticFilesHandler
from django.core.asgi import get_asgi_application
from django.core.exceptions import SuspiciousFileOperation
from django.http import Http404

from config.security import browser_security_headers

if TYPE_CHECKING:
    from asgiref.typing import ASGI3Application
    from django.core.handlers.asgi import ASGIHandler
    from django.http import FileResponse, HttpRequest, HttpResponse
    from django.http.response import HttpResponseBase


class DocumentationStaticFilesHandler(ASGIStaticFilesHandler):
    """Serve local documentation assets through Django's ASGI static handler.

    Inherits ``ASGIStaticFilesHandler`` and converts unsafe finder paths into ordinary not-found
    responses. It adds no attributes and customizes response security plus ``serve``.

    Attributes:
        None.

    Members:
        get_response_async: Attach browser hardening to a static response.
        serve: Resolve a safe static finder resource.
    """

    @override
    async def get_response_async(self, request: HttpRequest) -> HttpResponseBase:
        """Attach the browser hardening contract to one static response.

        Preserves Django's streaming and conditional response behavior while restoring the common
        browser headers that static interception intentionally serves outside Django middleware.

        Arguments:
            request: Django request for a path beneath ``STATIC_URL``.

        Returns:
            Static response carrying the project-owned security headers.
        """
        response = await super().get_response_async(request)
        for name, value in browser_security_headers().items():
            response.headers.setdefault(name, value)

        return response

    @override
    def serve(self, request: HttpRequest) -> HttpResponse | FileResponse:
        """Serve one static finder resource without exposing unsafe paths.

        Delegates content types, conditional caching, and file streaming to Django while mapping
        rejected finder paths to the same public response as an unknown static resource.

        Arguments:
            request: Django request for a path beneath ``STATIC_URL``.

        Returns:
            Static file response.

        Raises:
            Http404: If the path is unsafe or no finder resource exists.
        """
        try:
            return super().serve(request)
        except SuspiciousFileOperation as error:
            raise Http404 from error


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
if settings.API_DOCUMENTATION_ENABLED:
    http_application = cast(
        "ASGI3Application",
        DocumentationStaticFilesHandler(cast("ASGIHandler", http_application)),
    )

application = ProtocolTypeRouter(
    {
        "http": http_application,
        "websocket": routing_module.websocket_application,
    }
)
