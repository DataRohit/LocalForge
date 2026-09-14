"""Settings shared by every environment.

Reads every credential, hostname, and environment-dependent value from the process environment
through the parsing library, so no module carries a literal secret and a missing required variable
fails at startup naming itself.
"""

from pathlib import Path
from typing import Any

import environ
from botocore.config import Config

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env()

SECRET_KEY = env.str("DJANGO_SECRET_KEY")

ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS")

CSRF_TRUSTED_ORIGINS = env.list("DJANGO_CSRF_TRUSTED_ORIGINS")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "health_check",
    "accounts",
    "channels",
    "storages",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
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
        "ENGINE": "django.db.backends.postgresql",
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
        "ENGINE": "django.db.backends.postgresql",
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
        "task_arguments": {
            "()": "config.logs.TaskArgumentRedactionFilter",
        },
    },
    "handlers": {
        "stdout": {
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stdout",
            "formatter": "structured",
        },
        "queue": {
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stdout",
            "formatter": "structured",
            "filters": ["task_arguments"],
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
    },
}
