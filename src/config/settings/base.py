"""Settings shared by every environment.

Reads every credential, hostname, and environment-dependent value from the process environment
through the parsing library, so no module carries a literal secret and a missing required variable
fails at startup naming itself.
"""

from pathlib import Path
from typing import Any

import environ

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
        "DIRS": [],
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

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOG_LEVEL = env.str("DJANGO_LOG_LEVEL", default="INFO")

LOGGING: dict[str, Any] = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "structured": {
            "()": "config.logs.StructuredFormatter",
        },
    },
    "handlers": {
        "stdout": {
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stdout",
            "formatter": "structured",
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
    },
}
