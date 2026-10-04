"""Settings for the testing environment.

Inherits every shared value and pins the behaviour the suite depends on, so a test result never
varies with a value an operator set in an environment file.
"""

from config.settings.base import *

DEBUG = False
