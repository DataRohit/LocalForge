"""Settings for the development environment.

Inherits every shared value and adds only what development changes, so the difference between the
two environments is visible in one short module rather than spread through conditionals.
"""

from config.settings.base import *
from config.settings.base import env

DEBUG = env.bool("DJANGO_DEBUG")
