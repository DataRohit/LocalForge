"""WSGI entry point for the project.

Exposes the application object a WSGI server serves, retained for tooling that does not speak ASGI
and defaulting to the development settings like the other entry points.
"""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")

application = get_wsgi_application()
