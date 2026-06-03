"""Django settings for Octonomy."""

from __future__ import annotations

import os
import warnings
from pathlib import Path

import dj_database_url
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "local-dev-secret")
DEBUG = env_bool("DJANGO_DEBUG", True)
ALLOWED_HOSTS = [
    host.strip() for host in os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
]

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.postgres",
    "rest_framework",
    "drf_spectacular",
    "octonomy.service_auth",
    "octonomy.audit",
    "octonomy.events",
    "octonomy.core",
    "octonomy.tags",
    "octonomy.assignments",
    "octonomy.openapi",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
    "octonomy.core.middleware.RequestContextMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
            ],
        },
    }
]

DATABASES = {
    "default": dj_database_url.config(
        default=os.getenv(
            "DATABASE_URL",
            "postgres://octonomy:octonomy@localhost:5432/octonomy",
        ),
        conn_max_age=60,
    )
}

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": ["octonomy.core.auth.BearerTokenPermission"],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "EXCEPTION_HANDLER": "octonomy.core.errors.exception_handler",
    "DEFAULT_PAGINATION_CLASS": "octonomy.core.pagination.OctonomyLimitOffsetPagination",
    "PAGE_SIZE": 50,
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Octonomy API",
    "DESCRIPTION": "Multi-tenant tag management and taxonomy service.",
    "VERSION": "0.1.0",
    "SERVE_INCLUDE_SCHEMA": False,
}

SERVICE_TOKEN_PEPPER = os.getenv("SERVICE_TOKEN_PEPPER", "")
if not DEBUG and (
    not SERVICE_TOKEN_PEPPER or SERVICE_TOKEN_PEPPER == "local-dev-service-token-pepper"
):
    raise ImproperlyConfigured(
        "SERVICE_TOKEN_PEPPER must be set to a non-default value when DEBUG is False."
    )
if DEBUG and not SERVICE_TOKEN_PEPPER:
    warnings.warn(
        "SERVICE_TOKEN_PEPPER is empty; local service token hashes are not peppered.",
        stacklevel=2,
    )
MAX_BULK_TAGS = int(os.getenv("MAX_BULK_TAGS", "200"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "json": {
            "()": "octonomy.core.logging.JsonFormatter",
        }
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "json",
        }
    },
    "root": {
        "handlers": ["console"],
        "level": LOG_LEVEL,
    },
}
