"""Unit tests for the environment-aware settings package.

Covers the values each environment module must declare, the shared values both inherit, and the
failure a missing required variable produces, so a configuration mistake surfaces here rather than
at startup in a container.
"""

import importlib
import importlib.util
import os
import secrets
import sys
from pathlib import Path
from typing import TYPE_CHECKING
from unittest import mock

import pytest
from django.conf import settings as configured_settings
from django.core.exceptions import ImproperlyConfigured

from config import settings as settings_package

if TYPE_CHECKING:
    from types import ModuleType

SETTINGS_DIRECTORY = Path(str(settings_package.__file__)).resolve().parent

REQUIRED_ENVIRONMENT = {
    "DJANGO_SECRET_KEY": secrets.token_urlsafe(32),
    "DJANGO_DEBUG": "true",
    "DJANGO_ALLOWED_HOSTS": "localhost",
    "DJANGO_CSRF_TRUSTED_ORIGINS": "http://localhost:8080",
    "POSTGRES_DB": "localforge",
    "POSTGRES_USER": "localforge_app",
    "POSTGRES_PASSWORD": secrets.token_urlsafe(16),
    "POSTGRES_HOST": "postgres-pg3ka",
    "POSTGRES_PORT": "5432",
    "POSTGRES_REPLICA_HOST": "postgres-replica-pg6vy",
    "POSTGRES_REPLICA_PORT": "5432",
    "VALKEY_CACHE_HOST": "valkey-cache-vc5tn",
    "VALKEY_CACHE_PORT": "6379",
    "VALKEY_CACHE_PASSWORD": secrets.token_urlsafe(16),
    "VALKEY_CACHE_DB": "0",
    "VALKEY_RESULTS_DB": "1",
    "VALKEY_CHANNELS_HOST": "valkey-channels-vh8dm",
    "VALKEY_CHANNELS_PORT": "6379",
    "VALKEY_CHANNELS_PASSWORD": secrets.token_urlsafe(16),
    "CELERY_BROKER_URL": "amqp://broker:secret@rabbitmq-rq4sx:5672/localforge",
    "CELERY_RESULT_BACKEND": "redis://:secret@valkey-cache-vc5tn:6379/1",
    "CELERY_TASK_ALWAYS_EAGER": "false",
}

SECRET_LIKE_MARKERS = ("secret", "password", "token")


def _execute_file(name: str) -> ModuleType:
    """Execute one settings file under a throwaway module name.

    Loads the file from the settings package directory and runs it, so the executed copy never
    replaces the module the test session itself is configured from.

    Arguments:
        name: The settings module's file stem, such as ``base``.

    Returns:
        The freshly executed module object.

    Raises:
        ImproperlyConfigured: If the file requires a variable the environment does not carry.
    """
    spec = importlib.util.spec_from_file_location(
        f"settings_probe_{name}",
        SETTINGS_DIRECTORY / f"{name}.py",
    )
    assert spec is not None
    assert spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    return module


def _execute_module_in_isolation(name: str, environment: dict[str, str]) -> ModuleType:
    """Execute one settings module against a controlled environment.

    Executes a fresh base first and substitutes it for the imported one while the environment
    module runs, so the values an environment module inherits come from the environment under test
    rather than from the base the session was configured with.

    Arguments:
        name: The settings module's file stem, such as ``development``.
        environment: The complete process environment the module executes under.

    Returns:
        The freshly executed module object.

    Raises:
        ImproperlyConfigured: If the module requires a variable the environment does not carry.
    """
    with mock.patch.dict(os.environ, environment, clear=True):
        base = _execute_file("base")

        if name == "base":
            return base

        with mock.patch.dict(sys.modules, {"config.settings.base": base}):
            return _execute_file(name)


@pytest.mark.unit
def test_entry_point_settings_reference_the_project_modules() -> None:
    """Point the framework at the project's own modules.

    Confirms the root URL configuration and both server application paths resolve to this project
    rather than to a generated default left over from scaffolding, because a typo in the ASGI path
    surfaces only when a server starts.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If any setting does not name the project module.
    """
    assert configured_settings.ROOT_URLCONF == "config.urls"
    assert configured_settings.WSGI_APPLICATION == "config.wsgi.application"
    assert configured_settings.ASGI_APPLICATION == "config.asgi.application"


@pytest.mark.unit
def test_installed_applications_include_the_admin_and_its_dependencies() -> None:
    """Register the admin and the applications it requires.

    Confirms the admin is installed together with the authentication and content type
    applications it depends on, since the admin fails to load without them.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If any required application is absent.
    """
    required = {
        "django.contrib.admin",
        "django.contrib.auth",
        "django.contrib.contenttypes",
        "django.contrib.sessions",
    }

    assert required.issubset(set(configured_settings.INSTALLED_APPS))


@pytest.mark.unit
def test_timezone_support_is_enabled_and_the_zone_comes_from_the_environment() -> None:
    """Store datetimes as timezone-aware values.

    Confirms timezone support is on and the zone is the one the environment selects, because
    turning support off later silently reinterprets stored values and is effectively irreversible.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If timezone support is disabled or the zone ignores the environment.
    """
    assert configured_settings.USE_TZ is True
    assert os.environ.get("DJANGO_TIME_ZONE", "UTC") == configured_settings.TIME_ZONE


@pytest.mark.unit
def test_password_hashing_prefers_a_memory_hard_algorithm() -> None:
    """Hash passwords with the memory-hard algorithm first.

    Confirms the first configured hasher is the memory-hard one and that its backing package is
    installed, since the framework refuses to use a hasher whose package is missing.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If another hasher is preferred.
        ImportError: If the backing package is not installed.
    """
    assert configured_settings.PASSWORD_HASHERS[0].endswith("Argon2PasswordHasher")
    assert importlib.import_module("argon2") is not None


@pytest.mark.unit
def test_the_database_is_configured_from_the_environment() -> None:
    """Reach the databases named by the environment.

    Confirms both aliases run on the PostgreSQL backend and take their name, role, host, and port
    from the environment rather than from a literal in a settings module.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the backend or any connection value does not match the environment.
    """
    aliases = _execute_module_in_isolation("base", REQUIRED_ENVIRONMENT).DATABASES

    assert set(aliases) == {"default", "replica"}

    for alias in ("default", "replica"):
        assert aliases[alias]["ENGINE"] == "django.db.backends.postgresql"
        assert aliases[alias]["NAME"] == REQUIRED_ENVIRONMENT["POSTGRES_DB"]
        assert aliases[alias]["USER"] == REQUIRED_ENVIRONMENT["POSTGRES_USER"]
        assert aliases[alias]["PASSWORD"] == REQUIRED_ENVIRONMENT["POSTGRES_PASSWORD"]

    assert aliases["default"]["HOST"] == REQUIRED_ENVIRONMENT["POSTGRES_HOST"]
    assert aliases["default"]["PORT"] == int(REQUIRED_ENVIRONMENT["POSTGRES_PORT"])
    assert aliases["replica"]["HOST"] == REQUIRED_ENVIRONMENT["POSTGRES_REPLICA_HOST"]
    assert aliases["replica"]["PORT"] == int(REQUIRED_ENVIRONMENT["POSTGRES_REPLICA_PORT"])
    assert configured_settings.DATABASES["default"]["ENGINE"] == aliases["default"]["ENGINE"]


@pytest.mark.unit
def test_logging_emits_structured_records_to_standard_output() -> None:
    """Ship logs the collector can index.

    Confirms the single handler writes to standard output through the project's structured
    formatter at the level the environment selects, which is what makes container logs queryable.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the handler, its formatter, or the level is not the configured one.
    """
    logging_configuration = configured_settings.LOGGING
    handler = logging_configuration["handlers"]["stdout"]
    formatter = logging_configuration["formatters"]["structured"]

    assert handler["stream"] == "ext://sys.stdout"
    assert handler["formatter"] == "structured"
    assert formatter["()"] == "config.logs.StructuredFormatter"
    assert logging_configuration["root"]["level"] == os.environ.get("DJANGO_LOG_LEVEL", "INFO")


@pytest.mark.unit
def test_the_testing_module_disables_debug_whatever_the_environment_says() -> None:
    """Keep tests independent of debug behaviour.

    Confirms the testing module pins debug off even when the environment asks for it, because a
    suite that passes only with debug on is asserting framework behaviour rather than its own.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the testing module honours the environment for this value.
    """
    module = _execute_module_in_isolation(
        "testing",
        REQUIRED_ENVIRONMENT | {"DJANGO_DEBUG": "true"},
    )

    assert module.DEBUG is False


@pytest.mark.unit
def test_the_development_module_enables_debug() -> None:
    """Run development with debug on.

    Confirms the development module enables debug, which is the setting the two environment
    modules exist to differ on, and inherits the shared values rather than restating them.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If debug is off or the module does not inherit the base values.
    """
    module = _execute_module_in_isolation("development", REQUIRED_ENVIRONMENT)

    assert module.DEBUG is True
    assert module.ROOT_URLCONF == "config.urls"
    assert module.DATABASES["default"]["HOST"] == REQUIRED_ENVIRONMENT["POSTGRES_HOST"]


@pytest.mark.unit
def test_the_two_environment_modules_differ_where_they_must() -> None:
    """Declare the difference between the environments.

    Confirms the two modules disagree on debug while agreeing on the shared values they both take
    from the base, so the split is a declaration rather than two divergent copies.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the modules agree on debug or disagree on a shared value.
    """
    development = _execute_module_in_isolation("development", REQUIRED_ENVIRONMENT)
    testing = _execute_module_in_isolation("testing", REQUIRED_ENVIRONMENT)

    assert development.DEBUG != testing.DEBUG
    assert development.INSTALLED_APPS == testing.INSTALLED_APPS
    assert development.DATABASES == testing.DATABASES
    assert development.DATABASES["default"]["HOST"] == REQUIRED_ENVIRONMENT["POSTGRES_HOST"]
    assert testing.DATABASES["default"]["HOST"] == REQUIRED_ENVIRONMENT["POSTGRES_HOST"]


@pytest.mark.unit
def test_a_missing_required_variable_names_itself_at_startup() -> None:
    """Fail startup naming the missing variable.

    Confirms a required variable that is absent raises during import with the variable's name in
    the message, so a configuration error is not deferred into a runtime mystery.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If import succeeds or the message does not name the variable.
    """
    incomplete = {
        name: value for name, value in REQUIRED_ENVIRONMENT.items() if name != "POSTGRES_HOST"
    }

    with pytest.raises(ImproperlyConfigured, match="POSTGRES_HOST"):
        _execute_module_in_isolation("base", incomplete)


@pytest.mark.unit
@pytest.mark.parametrize("module_name", ["base", "development", "testing"])
def test_no_settings_module_carries_a_literal_credential(module_name: str) -> None:
    """Keep every credential out of the source.

    Confirms no settings module assigns a quoted value to a secret-like name, which is the failure
    the environment inventory exists to prevent and the one that is easy to miss in a diff.

    Arguments:
        module_name: The settings module's file stem.

    Returns:
        None.

    Raises:
        AssertionError: If a secret-like assignment carries a literal value.
    """
    source = (SETTINGS_DIRECTORY / f"{module_name}.py").read_text(encoding="utf-8")

    for line in source.splitlines():
        stripped = line.strip()
        name, separator, value = stripped.partition(" = ")
        if not separator:
            name, separator, value = stripped.partition(": ")

        if separator and any(marker in name.lower() for marker in SECRET_LIKE_MARKERS):
            assert not value.lstrip().startswith(('"', "'"))
