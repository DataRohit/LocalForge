"""Django project package.

Holds the settings package, the URL configuration, and the server entry points for the project the
application, worker, scheduler, and test-runner containers all run some form of.
"""

from config.celery import app as celery_app

__all__ = ["celery_app"]
