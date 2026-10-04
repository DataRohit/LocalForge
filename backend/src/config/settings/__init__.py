"""Environment-aware settings package.

Holds a shared base module plus one module per environment, and resolves the environment file a
process needs when it has no configuration of its own, so a container started from an environment
file is never topped up from a file that happens to be on a mounted source tree.
"""

from pathlib import Path
from typing import TYPE_CHECKING

import environ

if TYPE_CHECKING:
    from collections.abc import Mapping

_REPOSITORY_ROOT_CANDIDATE = Path(__file__).resolve().parents[4]
REPOSITORY_ROOT = (
    _REPOSITORY_ROOT_CANDIDATE
    if (_REPOSITORY_ROOT_CANDIDATE / ".env.example").is_file()
    else Path(__file__).resolve().parents[3]
)

CONFIGURED_MARKER = "DJANGO_SECRET_KEY"

ENVIRONMENT_FILES: Mapping[str, str] = {
    "config.settings.development": ".env.development",
    "config.settings.testing": ".env.testing.host",
}


def load_environment_file() -> Path | None:
    """Load the environment file belonging to the selected settings module.

    Does nothing when the process already carries configuration, which is how a container keeps the
    values Compose gave it and how a partially supplied environment still fails naming the variable
    it is missing; otherwise reads the file the selected module names, when that file is present.

    Arguments:
        None.

    Returns:
        The path that was read, or ``None`` when no file applied or the named file is absent.

    Raises:
        None.
    """
    env = environ.Env()

    if env.str(CONFIGURED_MARKER, default="") != "":
        return None

    filename = ENVIRONMENT_FILES.get(env.str("DJANGO_SETTINGS_MODULE", default=""))
    if filename is None:
        return None

    path = REPOSITORY_ROOT / filename
    if not path.is_file():
        return None

    environ.Env.read_env(str(path), overwrite=False)

    return path


LOADED_ENVIRONMENT_FILE = load_environment_file()
