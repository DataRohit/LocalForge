"""Observability-only ASGI application.

Builds a Django handler with the internal metrics URL table, used by the listener bound only to the
application container's observability-network address.
"""

import os
from typing import TYPE_CHECKING, cast

from django.conf import settings
from django.core.asgi import get_asgi_application

from config.logs import finalize_streaming_asgi

if TYPE_CHECKING:
    from asgiref.typing import ASGI3Application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")
settings.ROOT_URLCONF = "config.metrics_urls"

application = finalize_streaming_asgi(
    cast("ASGI3Application", get_asgi_application()),
)
