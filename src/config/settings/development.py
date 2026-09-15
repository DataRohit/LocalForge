"""Settings for the development environment.

Inherits every shared value and adds only what development changes, so the difference between the
two environments is visible in one short module rather than spread through conditionals.
"""

from config.settings.base import *
from config.settings.base import LOG_LEVEL, LOGGING, env

DEBUG = env.bool("DJANGO_DEBUG")

LOGGING["filters"]["redact_query_values"] = {"()": "config.logs.QueryRedactionFilter"}

LOGGING["handlers"]["queries"] = {
    "class": "logging.StreamHandler",
    "stream": "ext://sys.stdout",
    "formatter": "structured",
    "filters": ["request_context", "redact_query_values"],
}

LOGGING["loggers"]["django.db.backends"] = {
    "handlers": ["queries"],
    "level": LOG_LEVEL,
    "propagate": False,
}
