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
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, cast
from unittest import mock

import pytest
from django.conf import settings as configured_settings
from django.core.exceptions import ImproperlyConfigured
from django.views.debug import SafeExceptionReporterFilter

from config import settings as settings_package

if TYPE_CHECKING:
    from types import ModuleType

SETTINGS_DIRECTORY = Path(str(settings_package.__file__)).resolve().parent
BODY_LIMIT_PROBE_BYTES = 2048

REQUIRED_ENVIRONMENT = {
    "DJANGO_SECRET_KEY": secrets.token_urlsafe(32),
    "DJANGO_DEBUG": "true",
    "DJANGO_ALLOWED_HOSTS": "localhost,localforge.localhost",
    "DJANGO_CSRF_TRUSTED_ORIGINS": ("http://localhost:8080,http://localforge.localhost:8080"),
    "DJANGO_TRUSTED_PROXY_NETWORKS": "10.89.2.0/24",
    "DJANGO_API_REQUEST_BODY_MAX_BYTES": "1048576",
    "DJANGO_JWT_ACCESS_TOKEN_LIFETIME_SECONDS": "300",
    "DJANGO_JWT_REFRESH_TOKEN_LIFETIME_SECONDS": "86400",
    "DJANGO_JWT_SIGNING_KEY": secrets.token_urlsafe(32),
    "DJANGO_TOKEN_LOGIN_ACCOUNT_THROTTLE_RATE": "5/minute",
    "DJANGO_TOKEN_LOGIN_ADDRESS_THROTTLE_RATE": "30/minute",
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
    "EMAIL_BACKEND": "django.core.mail.backends.smtp.EmailBackend",
    "EMAIL_HOST": "mailpit-mp6gb",
    "EMAIL_PORT": "1025",
    "MAILPIT_WEB_PORT": "8025",
    "DEFAULT_FROM_EMAIL": "no-reply@localforge.invalid",
    "DJANGO_SITE_NAME": "LocalForge",
    "DJANGO_SITE_URL": "http://localforge.localhost:8080",
    "S3_ENDPOINT_URL": "http://seaweedfs-sw9cr:8333",
    "S3_ACCESS_KEY_ID": secrets.token_hex(20),
    "S3_SECRET_ACCESS_KEY": secrets.token_hex(20),
    "S3_BUCKET_NAME": "localforge-media",
    "S3_REGION_NAME": "us-east-1",
}

SECRET_LIKE_MARKERS = ("access_key", "secret", "password", "token")


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
def test_uploaded_media_uses_private_object_storage() -> None:
    """Keep uploaded files in the private media bucket.

    Confirms the default backend takes every connection value from the environment, overwrites
    colliding names deliberately, and signs URLs while static assets retain their own backend.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If media or static storage does not follow the documented separation.
    """
    module = _execute_module_in_isolation("base", REQUIRED_ENVIRONMENT)
    media = module.STORAGES["default"]
    options = media["OPTIONS"]

    assert media["BACKEND"] == "storages.backends.s3.S3Storage"
    assert options["endpoint_url"] == REQUIRED_ENVIRONMENT["S3_ENDPOINT_URL"]
    assert options["access_key"] == REQUIRED_ENVIRONMENT["S3_ACCESS_KEY_ID"]
    assert options["secret_key"] == REQUIRED_ENVIRONMENT["S3_SECRET_ACCESS_KEY"]
    assert options["bucket_name"] == REQUIRED_ENVIRONMENT["S3_BUCKET_NAME"]
    assert options["region_name"] == REQUIRED_ENVIRONMENT["S3_REGION_NAME"]
    client_config = cast("dict[str, object]", vars(options["client_config"]))
    assert client_config["connect_timeout"] == module.S3_CONNECT_TIMEOUT_SECONDS
    assert client_config["read_timeout"] == module.S3_READ_TIMEOUT_SECONDS
    assert client_config["retries"] == {"total_max_attempts": 1, "mode": "standard"}
    assert client_config["signature_version"] == "s3v4"
    assert client_config["s3"] == {"addressing_style": "path"}
    assert options["default_acl"] is None
    assert options["file_overwrite"] is True
    assert options["location"] == "media"
    assert options["querystring_auth"] is True
    assert (
        module.STORAGES["staticfiles"]["BACKEND"]
        == "django.contrib.staticfiles.storage.StaticFilesStorage"
    )
    assert not hasattr(module, "MEDIA_ROOT")

    storage_class = importlib.import_module("storages.backends.s3").S3Storage
    storage = storage_class(**options)
    effective_retries = storage.connection.meta.client.meta.config.retries
    assert effective_retries == {"total_max_attempts": 1, "mode": "standard"}


@pytest.mark.unit
def test_email_delivery_uses_environment_configuration() -> None:
    """Point application email at the configured local capture service.

    Confirms the backend, SMTP address, sender, and site identity all come from the environment,
    while application templates are discoverable outside an installed Django application.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If any mail or site setting ignores its environment variable.
    """
    module = _execute_module_in_isolation("base", REQUIRED_ENVIRONMENT)

    assert REQUIRED_ENVIRONMENT["EMAIL_BACKEND"] == module.EMAIL_BACKEND
    assert REQUIRED_ENVIRONMENT["EMAIL_HOST"] == module.EMAIL_HOST
    assert int(REQUIRED_ENVIRONMENT["EMAIL_PORT"]) == module.EMAIL_PORT
    assert int(REQUIRED_ENVIRONMENT["MAILPIT_WEB_PORT"]) == module.MAILPIT_WEB_PORT
    assert REQUIRED_ENVIRONMENT["DEFAULT_FROM_EMAIL"] == module.DEFAULT_FROM_EMAIL
    assert REQUIRED_ENVIRONMENT["DJANGO_SITE_NAME"] == module.SITE_NAME
    assert REQUIRED_ENVIRONMENT["DJANGO_SITE_URL"] == module.SITE_URL
    assert module.TEMPLATES[0]["DIRS"] == [module.BASE_DIR / "config" / "templates"]


@pytest.mark.unit
def test_development_accepts_the_proxy_host_and_origin() -> None:
    """Accept browser and load-balancer traffic through the registered proxy host.

    Confirms allowed hosts, trusted origins, and generated absolute links use one public
    development address while retaining direct localhost browser access.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the proxy host is rejected or email links bypass it.
    """
    module = _execute_module_in_isolation("base", REQUIRED_ENVIRONMENT)

    assert module.ALLOWED_HOSTS == ["localhost", "localforge.localhost"]
    assert module.CSRF_TRUSTED_ORIGINS == [
        "http://localhost:8080",
        "http://localforge.localhost:8080",
    ]
    assert tuple(str(network) for network in module.TRUSTED_PROXY_NETWORKS) == ("10.89.2.0/24",)
    assert module.SITE_URL == "http://localforge.localhost:8080"


@pytest.mark.unit
def test_storage_credentials_are_hidden_from_debug_settings() -> None:
    """Keep object-storage credentials out of diagnostic output.

    Runs Django's own settings cleanser over the configured storage mapping, so nested credentials
    remain hidden if an exception page or error report includes the setting.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If either configured credential survives the cleanser.
    """
    module = _execute_module_in_isolation("base", REQUIRED_ENVIRONMENT)
    reporter_filter = SafeExceptionReporterFilter()
    safe_storage = cast(
        "dict[str, dict[str, object]]",
        reporter_filter.cleanse_setting("STORAGES", module.STORAGES),
    )
    options = cast("dict[str, object]", safe_storage["default"]["OPTIONS"])

    assert options["access_key"] == reporter_filter.cleansed_substitute
    assert options["secret_key"] == reporter_filter.cleansed_substitute


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
def test_each_password_hasher_declares_one_accepted_profile_policy() -> None:
    """Bind every configured password hasher to an explicit accepted profile.

    Confirms multi-parameter hashers accept only their atomic current profile while both PBKDF2
    variants additionally permit lower iterations that runtime hardening can top up exactly.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If a configured hasher lacks or broadens its accepted-profile policy.
    """
    assert configured_settings.PASSWORD_HASH_ACCEPTED_PROFILES == {
        "argon2": "current-exact",
        "pbkdf2_sha256": "current-or-lower-iterations",
        "pbkdf2_sha1": "current-or-lower-iterations",
        "scrypt": "current-exact",
    }


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
        assert aliases[alias]["ENGINE"] == "django_prometheus.db.backends.postgresql"
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
    assert handler["filters"] == ["request_context"]
    assert formatter["()"] == "config.logs.StructuredFormatter"
    assert logging_configuration["root"]["level"] == os.environ.get("DJANGO_LOG_LEVEL", "INFO")
    assert logging_configuration["loggers"]["uvicorn"]["handlers"] == ["stdout"]
    assert logging_configuration["loggers"]["uvicorn.error"]["handlers"] == ["stdout"]
    assert logging_configuration["loggers"]["uvicorn.access"]["handlers"] == []


@pytest.mark.unit
def test_request_and_database_instrumentation_wrap_the_application() -> None:
    """Enable application and database metrics in the required order.

    Confirms django-prometheus owns both database engines and brackets the middleware stack, while
    request correlation remains inside its outer timing boundary.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If instrumentation is absent or ordered incorrectly.
    """
    middleware = configured_settings.MIDDLEWARE

    assert configured_settings.INSTALLED_APPS[0] == "django_prometheus"
    assert middleware[0] == "django_prometheus.middleware.PrometheusBeforeMiddleware"
    assert middleware[1] == "config.logs.request_context_middleware"
    assert middleware[2] == "config.api.api_request_body_limit_middleware"
    assert middleware[3] == "config.api.api_error_envelope_middleware"
    assert "config.api.ApiCommonMiddleware" in middleware
    assert "django.middleware.common.CommonMiddleware" not in middleware
    assert middleware[-1] == "config.logs.BoundedPrometheusAfterMiddleware"
    assert all(
        database["ENGINE"] == "django_prometheus.db.backends.postgresql"
        for database in configured_settings.DATABASES.values()
    )


@pytest.mark.unit
def test_api_request_body_limit_comes_from_the_environment() -> None:
    """Configure the API request ceiling from the environment.

    Executes shared settings with a distinct value and verifies middleware receives that integer,
    preventing a deployment-specific memory boundary from becoming a code literal.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the body ceiling ignores or misparses the environment.
    """
    module = _execute_module_in_isolation(
        "base",
        REQUIRED_ENVIRONMENT | {"DJANGO_API_REQUEST_BODY_MAX_BYTES": str(BODY_LIMIT_PROBE_BYTES)},
    )

    assert module.API_REQUEST_BODY_MAX_BYTES == BODY_LIMIT_PROBE_BYTES
    assert module.FILE_UPLOAD_MAX_MEMORY_SIZE == BODY_LIMIT_PROBE_BYTES


@pytest.mark.unit
def test_shared_rest_framework_defaults_close_new_routes() -> None:
    """Configure the API's secure defaults in one place.

    Confirms authentication, permission, pagination, rendering, schema, and exception handling are
    inherited centrally, so a route must explicitly opt out when its contract differs.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If a new route would be public or redeclare shared policy.
    """
    configuration = configured_settings.REST_FRAMEWORK

    assert configuration == {
        "DEFAULT_AUTHENTICATION_CLASSES": [
            "accounts.authentication.JWTAuthentication",
            "accounts.authentication.PrimaryTokenAuthentication",
        ],
        "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
        "DEFAULT_PERMISSION_CLASSES": [
            "rest_framework.permissions.IsAuthenticated",
        ],
        "DEFAULT_RENDERER_CLASSES": [
            "rest_framework.renderers.JSONRenderer",
        ],
        "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
        "EXCEPTION_HANDLER": "config.api.api_exception_handler",
        "PAGE_SIZE": 100,
    }

    assert (
        REQUIRED_ENVIRONMENT["DJANGO_TOKEN_LOGIN_ACCOUNT_THROTTLE_RATE"]
        == configured_settings.TOKEN_LOGIN_ACCOUNT_THROTTLE_RATE
    )
    assert (
        REQUIRED_ENVIRONMENT["DJANGO_TOKEN_LOGIN_ADDRESS_THROTTLE_RATE"]
        == configured_settings.TOKEN_LOGIN_ADDRESS_THROTTLE_RATE
    )
    assert configured_settings.LOGIN_THROTTLE_DATABASE_ALIAS == "default"
    assert set(configured_settings.CACHES) == {"default", "sessions"}


@pytest.mark.unit
def test_json_web_token_policy_comes_from_distinct_environment_values() -> None:
    """Configure the complete JSON web token policy from the environment.

    Executes shared settings with independent signing and lifetime values, proving access expires
    first, refresh rotation blacklists replay, and the framework secret is not reused.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If any JSON web token policy value is absent or unsafe.
    """
    module = _execute_module_in_isolation("base", REQUIRED_ENVIRONMENT)

    assert "rest_framework_simplejwt.token_blacklist" in module.INSTALLED_APPS
    assert REQUIRED_ENVIRONMENT["DJANGO_JWT_SIGNING_KEY"] == module.JWT_SIGNING_KEY
    assert module.JWT_SIGNING_KEY != module.SECRET_KEY
    assert {
        "ACCESS_TOKEN_LIFETIME": timedelta(seconds=300),
        "REFRESH_TOKEN_LIFETIME": timedelta(seconds=86400),
        "ROTATE_REFRESH_TOKENS": True,
        "BLACKLIST_AFTER_ROTATION": True,
        "ALGORITHM": "HS256",
        "SIGNING_KEY": REQUIRED_ENVIRONMENT["DJANGO_JWT_SIGNING_KEY"],
        "AUTH_HEADER_TYPES": ("Bearer",),
        "USER_ID_FIELD": "id",
        "USER_ID_CLAIM": "user_id",
        "AUTH_TOKEN_CLASSES": ("rest_framework_simplejwt.tokens.AccessToken",),
        "CHECK_USER_IS_ACTIVE": True,
        "CHECK_REVOKE_TOKEN": False,
    } == module.SIMPLE_JWT


@pytest.mark.unit
@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        pytest.param(
            {"DJANGO_JWT_SIGNING_KEY": REQUIRED_ENVIRONMENT["DJANGO_SECRET_KEY"]},
            "DJANGO_JWT_SIGNING_KEY",
            id="shared-signing-key",
        ),
        pytest.param(
            {"DJANGO_JWT_SIGNING_KEY": ""},
            "DJANGO_JWT_SIGNING_KEY",
            id="empty-signing-key",
        ),
        pytest.param(
            {"DJANGO_JWT_ACCESS_TOKEN_LIFETIME_SECONDS": "0"},
            "must be positive",
            id="nonpositive-access-lifetime",
        ),
        pytest.param(
            {
                "DJANGO_JWT_ACCESS_TOKEN_LIFETIME_SECONDS": "86400",
                "DJANGO_JWT_REFRESH_TOKEN_LIFETIME_SECONDS": "300",
            },
            "must be shorter",
            id="reversed-lifetimes",
        ),
    ],
)
def test_json_web_token_settings_reject_unsafe_relationships(
    overrides: dict[str, str],
    message: str,
) -> None:
    """Reject shared signing material and non-shorter access lifetimes.

    Executes shared settings with each unsafe relationship and verifies startup fails before a
    worker can issue credentials under the wrong security policy.

    Arguments:
        overrides: Unsafe environment values replacing the valid baseline.
        message: Diagnostic fragment the configuration error must carry.

    Returns:
        None.

    Raises:
        AssertionError: If unsafe JSON web token settings load successfully.
    """
    with pytest.raises(ImproperlyConfigured, match=message):
        _execute_module_in_isolation("base", REQUIRED_ENVIRONMENT | overrides)


@pytest.mark.unit
def test_browsable_rendering_is_development_only() -> None:
    """Expose browser-oriented rendering only in the interactive environment.

    Executes both settings modules in isolation and verifies development adds the browsable
    renderer while testing remains JSON-only and therefore headless.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If renderer availability leaks across environments.
    """
    development = _execute_module_in_isolation("development", REQUIRED_ENVIRONMENT)
    testing = _execute_module_in_isolation("testing", REQUIRED_ENVIRONMENT)

    assert development.REST_FRAMEWORK["DEFAULT_RENDERER_CLASSES"] == [
        "rest_framework.renderers.JSONRenderer",
        "rest_framework.renderers.BrowsableAPIRenderer",
    ]
    assert testing.REST_FRAMEWORK["DEFAULT_RENDERER_CLASSES"] == [
        "rest_framework.renderers.JSONRenderer",
    ]


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
def test_development_database_logs_follow_the_environment_level() -> None:
    """Apply the configured application threshold to database query records.

    Confirms raising the environment log level suppresses lower-severity query records before they
    reach stdout and Loki, rather than leaving the development query handler permanently at debug.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the database logger ignores the environment level.
    """
    module = _execute_module_in_isolation(
        "development",
        REQUIRED_ENVIRONMENT | {"DJANGO_LOG_LEVEL": "WARNING"},
    )

    assert module.LOGGING["loggers"]["django.db.backends"]["level"] == "WARNING"


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
