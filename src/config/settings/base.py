"""Settings shared by every environment.

Reads every credential, hostname, and environment-dependent value from the process environment
through the parsing library, so no module carries a literal secret and a missing required variable
fails at startup naming itself.
"""

import math
from datetime import timedelta
from ipaddress import ip_network
from pathlib import Path
from typing import Any

import environ
from botocore.config import Config
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env()

SECRET_KEY = env.str("DJANGO_SECRET_KEY")
JWT_SIGNING_KEY = env.str("DJANGO_JWT_SIGNING_KEY")
JWT_ACCESS_TOKEN_LIFETIME_SECONDS = env.int("DJANGO_JWT_ACCESS_TOKEN_LIFETIME_SECONDS")
JWT_REFRESH_TOKEN_LIFETIME_SECONDS = env.int("DJANGO_JWT_REFRESH_TOKEN_LIFETIME_SECONDS")
ACCOUNT_ACTIVATION_TOKEN_LIFETIME_SECONDS = env.int(
    "DJANGO_ACCOUNT_ACTIVATION_TOKEN_LIFETIME_SECONDS"
)
PASSWORD_RESET_TIMEOUT = env.int("DJANGO_PASSWORD_RESET_TOKEN_LIFETIME_SECONDS")
USERNAME_RESET_TIMEOUT = env.int("DJANGO_USERNAME_RESET_TOKEN_LIFETIME_SECONDS")

if not JWT_SIGNING_KEY:
    message = "DJANGO_JWT_SIGNING_KEY must not be empty"
    raise ImproperlyConfigured(message)

if JWT_SIGNING_KEY == SECRET_KEY:
    message = "DJANGO_JWT_SIGNING_KEY must differ from DJANGO_SECRET_KEY"
    raise ImproperlyConfigured(message)

if min(JWT_ACCESS_TOKEN_LIFETIME_SECONDS, JWT_REFRESH_TOKEN_LIFETIME_SECONDS) <= 0:
    message = "JSON web token lifetimes must be positive"
    raise ImproperlyConfigured(message)

if JWT_ACCESS_TOKEN_LIFETIME_SECONDS >= JWT_REFRESH_TOKEN_LIFETIME_SECONDS:
    message = (
        "DJANGO_JWT_ACCESS_TOKEN_LIFETIME_SECONDS must be shorter than "
        "DJANGO_JWT_REFRESH_TOKEN_LIFETIME_SECONDS"
    )
    raise ImproperlyConfigured(message)

if ACCOUNT_ACTIVATION_TOKEN_LIFETIME_SECONDS <= 0:
    message = "DJANGO_ACCOUNT_ACTIVATION_TOKEN_LIFETIME_SECONDS must be positive"
    raise ImproperlyConfigured(message)

if PASSWORD_RESET_TIMEOUT <= 0:
    message = "DJANGO_PASSWORD_RESET_TOKEN_LIFETIME_SECONDS must be positive"
    raise ImproperlyConfigured(message)

if USERNAME_RESET_TIMEOUT <= 0:
    message = "DJANGO_USERNAME_RESET_TOKEN_LIFETIME_SECONDS must be positive"
    raise ImproperlyConfigured(message)

ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS")

CSRF_TRUSTED_ORIGINS = env.list("DJANGO_CSRF_TRUSTED_ORIGINS")

_trusted_proxy_networks = env.str("DJANGO_TRUSTED_PROXY_NETWORKS")
TRUSTED_PROXY_NETWORKS = (
    ()
    if _trusted_proxy_networks == "none"
    else tuple(
        ip_network(value.strip(), strict=False)
        for value in _trusted_proxy_networks.split(",")
        if value.strip()
    )
)

API_REQUEST_BODY_MAX_BYTES = env.int("DJANGO_API_REQUEST_BODY_MAX_BYTES")
FILE_UPLOAD_MAX_MEMORY_SIZE = API_REQUEST_BODY_MAX_BYTES

INSTALLED_APPS = [
    "django_prometheus",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "health_check",
    "accounts",
    "channels",
    "rest_framework",
    "rest_framework.authtoken",
    "rest_framework_simplejwt.token_blacklist",
    "drf_spectacular",
    "storages",
]

REST_FRAMEWORK = {
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

TOKEN_LOGIN_ACCOUNT_THROTTLE_RATE = env.str("DJANGO_TOKEN_LOGIN_ACCOUNT_THROTTLE_RATE")
TOKEN_LOGIN_ADDRESS_THROTTLE_RATE = env.str("DJANGO_TOKEN_LOGIN_ADDRESS_THROTTLE_RATE")
USER_REGISTRATION_ADDRESS_THROTTLE_RATE = env.str("DJANGO_USER_REGISTRATION_ADDRESS_THROTTLE_RATE")
USER_REGISTRATION_MINIMUM_RESPONSE_DURATION_SECONDS = float(
    env.str("DJANGO_USER_REGISTRATION_MINIMUM_RESPONSE_DURATION_SECONDS")
)
ACCOUNT_ACTIVATION_RESEND_ADDRESS_THROTTLE_RATE = env.str(
    "DJANGO_ACCOUNT_ACTIVATION_RESEND_ADDRESS_THROTTLE_RATE"
)
ACCOUNT_ACTIVATION_RESEND_ACCOUNT_THROTTLE_RATE = env.str(
    "DJANGO_ACCOUNT_ACTIVATION_RESEND_ACCOUNT_THROTTLE_RATE"
)
PASSWORD_RESET_MINIMUM_RESPONSE_DURATION_SECONDS = float(
    env.str("DJANGO_PASSWORD_RESET_MINIMUM_RESPONSE_DURATION_SECONDS")
)
PASSWORD_RESET_ADDRESS_THROTTLE_RATE = env.str("DJANGO_PASSWORD_RESET_ADDRESS_THROTTLE_RATE")
PASSWORD_RESET_ACCOUNT_THROTTLE_RATE = env.str("DJANGO_PASSWORD_RESET_ACCOUNT_THROTTLE_RATE")
USERNAME_RESET_MINIMUM_RESPONSE_DURATION_SECONDS = float(
    env.str("DJANGO_USERNAME_RESET_MINIMUM_RESPONSE_DURATION_SECONDS")
)
USERNAME_RESET_ADDRESS_THROTTLE_RATE = env.str("DJANGO_USERNAME_RESET_ADDRESS_THROTTLE_RATE")
USERNAME_RESET_ACCOUNT_THROTTLE_RATE = env.str("DJANGO_USERNAME_RESET_ACCOUNT_THROTTLE_RATE")
LOGIN_THROTTLE_DATABASE_ALIAS = "default"

if (
    not math.isfinite(USER_REGISTRATION_MINIMUM_RESPONSE_DURATION_SECONDS)
    or USER_REGISTRATION_MINIMUM_RESPONSE_DURATION_SECONDS <= 0
):
    message = (
        "DJANGO_USER_REGISTRATION_MINIMUM_RESPONSE_DURATION_SECONDS must be finite and positive"
    )
    raise ImproperlyConfigured(message)

if (
    not math.isfinite(USERNAME_RESET_MINIMUM_RESPONSE_DURATION_SECONDS)
    or USERNAME_RESET_MINIMUM_RESPONSE_DURATION_SECONDS <= 0
):
    message = "DJANGO_USERNAME_RESET_MINIMUM_RESPONSE_DURATION_SECONDS must be finite and positive"
    raise ImproperlyConfigured(message)

if (
    not math.isfinite(PASSWORD_RESET_MINIMUM_RESPONSE_DURATION_SECONDS)
    or PASSWORD_RESET_MINIMUM_RESPONSE_DURATION_SECONDS <= 0
):
    message = "DJANGO_PASSWORD_RESET_MINIMUM_RESPONSE_DURATION_SECONDS must be finite and positive"
    raise ImproperlyConfigured(message)

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(seconds=JWT_ACCESS_TOKEN_LIFETIME_SECONDS),
    "REFRESH_TOKEN_LIFETIME": timedelta(seconds=JWT_REFRESH_TOKEN_LIFETIME_SECONDS),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "ALGORITHM": "HS256",
    "SIGNING_KEY": JWT_SIGNING_KEY,
    "AUTH_HEADER_TYPES": ("Bearer",),
    "USER_ID_FIELD": "id",
    "USER_ID_CLAIM": "user_id",
    "AUTH_TOKEN_CLASSES": ("rest_framework_simplejwt.tokens.AccessToken",),
    "CHECK_USER_IS_ACTIVE": True,
    "CHECK_REVOKE_TOKEN": True,
}

CSRF_FAILURE_VIEW = "config.api.api_csrf_failure"

MIDDLEWARE = [
    "django_prometheus.middleware.PrometheusBeforeMiddleware",
    "config.logs.request_context_middleware",
    "config.api.api_request_body_limit_middleware",
    "config.api.api_error_envelope_middleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "config.api.ApiCommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "config.logs.BoundedPrometheusAfterMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "config" / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

AUTH_USER_MODEL = "accounts.User"

DATABASE_WEB_CONNECTION_BUDGET = 32
DATABASE_POOL_MINIMUM_SIZE = 2
DATABASE_CONNECTION_MAX_LIFETIME_SECONDS = 600
DATABASE_POOL_CHECKOUT_TIMEOUT_SECONDS = 3
DATABASE_CONNECT_TIMEOUT_SECONDS = 5

_uvicorn_workers = env.int("UVICORN_WORKERS", default=2)
_database_aliases = 2

_database_pool = {
    "min_size": 1,
    "max_size": max(
        DATABASE_POOL_MINIMUM_SIZE,
        DATABASE_WEB_CONNECTION_BUDGET // (_uvicorn_workers * _database_aliases),
    ),
    "max_lifetime": DATABASE_CONNECTION_MAX_LIFETIME_SECONDS,
    "timeout": DATABASE_POOL_CHECKOUT_TIMEOUT_SECONDS,
}

_database_options = {
    "pool": _database_pool,
    "connect_timeout": DATABASE_CONNECT_TIMEOUT_SECONDS,
}

DATABASES = {
    "default": {
        "ENGINE": "django_prometheus.db.backends.postgresql",
        "NAME": env.str("POSTGRES_DB"),
        "USER": env.str("POSTGRES_USER"),
        "PASSWORD": env.str("POSTGRES_PASSWORD"),
        "HOST": env.str("POSTGRES_HOST"),
        "PORT": env.int("POSTGRES_PORT"),
        "CONN_MAX_AGE": 0,
        "CONN_HEALTH_CHECKS": True,
        "OPTIONS": _database_options,
    },
    "replica": {
        "ENGINE": "django_prometheus.db.backends.postgresql",
        "NAME": env.str("POSTGRES_DB"),
        "USER": env.str("POSTGRES_USER"),
        "PASSWORD": env.str("POSTGRES_PASSWORD"),
        "HOST": env.str("POSTGRES_REPLICA_HOST"),
        "PORT": env.int("POSTGRES_REPLICA_PORT"),
        "CONN_MAX_AGE": 0,
        "CONN_HEALTH_CHECKS": True,
        "OPTIONS": _database_options,
        "TEST": {"MIRROR": "default"},
    },
}

DATABASE_ROUTERS = ["config.db_router.PrimaryReplicaRouter"]

CACHE_KEY_PREFIX = "localforge"
CACHE_DEFAULT_TIMEOUT_SECONDS = 300
CACHE_SOCKET_TIMEOUT_SECONDS = 3

_cache_location = (
    f"redis://{env.str('VALKEY_CACHE_HOST')}:{env.int('VALKEY_CACHE_PORT')}"
    f"/{env.int('VALKEY_CACHE_DB')}"
)

_cache_options = {
    "password": env.str("VALKEY_CACHE_PASSWORD"),
    "socket_connect_timeout": CACHE_SOCKET_TIMEOUT_SECONDS,
    "socket_timeout": CACHE_SOCKET_TIMEOUT_SECONDS,
}

CACHES = {
    "default": {
        "BACKEND": "config.cache.ResilientRedisCache",
        "LOCATION": _cache_location,
        "KEY_PREFIX": CACHE_KEY_PREFIX,
        "TIMEOUT": CACHE_DEFAULT_TIMEOUT_SECONDS,
        "OPTIONS": _cache_options,
    },
    "sessions": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": _cache_location,
        "KEY_PREFIX": f"{CACHE_KEY_PREFIX}-session",
        "OPTIONS": _cache_options,
    },
}

SESSION_ENGINE = "django.contrib.sessions.backends.cache"
SESSION_CACHE_ALIAS = "sessions"

ASGI_APPLICATION = "config.asgi.application"

CHANNEL_LAYER_PREFIX = "localforge"

CHANNEL_LAYER_SOCKET_TIMEOUT_SECONDS = 5

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "config.channels.ConfirmedRedisPubSubChannelLayer",
        "CONFIG": {
            "hosts": [
                {
                    "host": env.str("VALKEY_CHANNELS_HOST"),
                    "port": env.int("VALKEY_CHANNELS_PORT"),
                    "password": env.str("VALKEY_CHANNELS_PASSWORD"),
                    "socket_connect_timeout": CHANNEL_LAYER_SOCKET_TIMEOUT_SECONDS,
                    "socket_timeout": CHANNEL_LAYER_SOCKET_TIMEOUT_SECONDS,
                },
            ],
            "prefix": CHANNEL_LAYER_PREFIX,
        },
    },
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher",
    "django.contrib.auth.hashers.ScryptPasswordHasher",
]

PASSWORD_HASH_ACCEPTED_PROFILES = {
    "argon2": "current-exact",
    "pbkdf2_sha256": "current-or-lower-iterations",
    "pbkdf2_sha1": "current-or-lower-iterations",
    "scrypt": "current-exact",
}

LANGUAGE_CODE = "en-us"

TIME_ZONE = env.str("DJANGO_TIME_ZONE", default="UTC")

USE_I18N = True

USE_TZ = True

STATIC_URL = "static/"

STATIC_ROOT = BASE_DIR.parent / "staticfiles"

EMAIL_BACKEND = env.str("EMAIL_BACKEND")
EMAIL_HOST = env.str("EMAIL_HOST")
EMAIL_PORT = env.int("EMAIL_PORT")
EMAIL_TIMEOUT = 10
DEFAULT_FROM_EMAIL = env.str("DEFAULT_FROM_EMAIL")
MAILPIT_WEB_PORT = env.int("MAILPIT_WEB_PORT")
SITE_NAME = env.str("DJANGO_SITE_NAME")
SITE_URL = env.str("DJANGO_SITE_URL")

S3_CONNECT_TIMEOUT_SECONDS = 5
S3_READ_TIMEOUT_SECONDS = 15

STORAGES = {
    "default": {
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": {
            "access_key": env.str("S3_ACCESS_KEY_ID"),
            "secret_key": env.str("S3_SECRET_ACCESS_KEY"),
            "bucket_name": env.str("S3_BUCKET_NAME"),
            "region_name": env.str("S3_REGION_NAME"),
            "endpoint_url": env.str("S3_ENDPOINT_URL"),
            "client_config": Config(
                connect_timeout=S3_CONNECT_TIMEOUT_SECONDS,
                read_timeout=S3_READ_TIMEOUT_SECONDS,
                retries={"total_max_attempts": 1, "mode": "standard"},
                signature_version="s3v4",
                s3={"addressing_style": "path"},
            ),
            "default_acl": None,
            "file_overwrite": True,
            "location": "media",
            "querystring_auth": True,
        },
    },
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

CELERY_TASK_TIME_LIMIT_SECONDS = 300
CELERY_TASK_SOFT_TIME_LIMIT_SECONDS = 270
CELERY_RESULT_EXPIRES_SECONDS = 86400

CELERY_BROKER_URL = env.str("CELERY_BROKER_URL")

CELERY_RESULT_BACKEND = env.str("CELERY_RESULT_BACKEND")

CELERY_TASK_ALWAYS_EAGER = env.bool("CELERY_TASK_ALWAYS_EAGER")

CELERY_TASK_EAGER_PROPAGATES = True

CELERY_ACCEPT_CONTENT = ["json"]

CELERY_TASK_SERIALIZER = "json"

CELERY_RESULT_SERIALIZER = "json"

CELERY_TASK_ACKS_LATE = True

CELERY_TASK_REJECT_ON_WORKER_LOST = True

CELERY_WORKER_PREFETCH_MULTIPLIER = 1

CELERY_TASK_TIME_LIMIT = CELERY_TASK_TIME_LIMIT_SECONDS

CELERY_TASK_SOFT_TIME_LIMIT = CELERY_TASK_SOFT_TIME_LIMIT_SECONDS

CELERY_RESULT_EXPIRES = CELERY_RESULT_EXPIRES_SECONDS

CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True

CELERY_CONTROL_QUEUE_EXCLUSIVE = True

CELERY_EVENT_QUEUE_EXCLUSIVE = True

CELERY_WORKER_HIJACK_ROOT_LOGGER = False

CELERY_TASK_PROTOCOL = 2

CELERY_TASK_SEND_SENT_EVENT = False

CELERY_TIMEZONE = TIME_ZONE

LOG_LEVEL = env.str("DJANGO_LOG_LEVEL", default="INFO")
LOGGING: dict[str, Any] = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "structured": {
            "()": "config.logs.StructuredFormatter",
        },
    },
    "filters": {
        "request_context": {
            "()": "config.logs.RequestContextFilter",
        },
        "task_arguments": {
            "()": "config.logs.TaskArgumentRedactionFilter",
        },
    },
    "handlers": {
        "stdout": {
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stdout",
            "formatter": "structured",
            "filters": ["request_context"],
        },
        "queue": {
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stdout",
            "formatter": "structured",
            "filters": ["request_context", "task_arguments"],
        },
    },
    "root": {
        "handlers": ["stdout"],
        "level": LOG_LEVEL,
    },
    "loggers": {
        "django": {
            "handlers": ["stdout"],
            "level": LOG_LEVEL,
            "propagate": False,
        },
        "celery": {
            "handlers": ["queue"],
            "level": LOG_LEVEL,
            "propagate": False,
        },
        "uvicorn": {
            "handlers": ["stdout"],
            "level": LOG_LEVEL,
            "propagate": False,
        },
        "uvicorn.error": {
            "handlers": ["stdout"],
            "level": LOG_LEVEL,
            "propagate": False,
        },
        "uvicorn.access": {
            "handlers": [],
            "level": LOG_LEVEL,
            "propagate": False,
        },
    },
}
