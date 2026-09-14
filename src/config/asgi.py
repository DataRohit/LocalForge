"""ASGI entry point for the project.

Exposes the application object an ASGI server serves, defaulting to the development settings so a
process started without an explicit selection runs the environment a developer expects.
"""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")

application = get_asgi_application()
