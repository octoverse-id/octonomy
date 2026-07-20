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


def env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ImproperlyConfigured(f"{name} must be an integer.") from exc


SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "local-dev-secret")
DEBUG = env_bool("DJANGO_DEBUG", True)
API_VERSION = os.getenv("OCTONOMY_API_VERSION", "1.0.0")
ALLOWED_HOSTS = [
    host.strip() for host in os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
]

if not DEBUG and SECRET_KEY == "local-dev-secret":
    raise ImproperlyConfigured(
        "DJANGO_SECRET_KEY must be set to a non-default value when DEBUG is False."
    )

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
    # One view tree serves both versions (the v1/v2 shim). The custom class also
    # resolves the request namespace scope from X-Namespace-* headers.
    "DEFAULT_VERSIONING_CLASS": "octonomy.core.versioning.NamespaceURLPathVersioning",
    "DEFAULT_VERSION": "v1",
    "ALLOWED_VERSIONS": ["v1", "v2"],
    "VERSION_PARAM": "version",
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Octonomy API",
    "DESCRIPTION": "Multi-tenant tag management and taxonomy service.",
    "VERSION": API_VERSION,
    "SERVE_INCLUDE_SCHEMA": False,
    # Namespace headers + include_global belong to the v2 contract only; the hook
    # injects them when generating the v2 schema and leaves v1 untouched.
    "POSTPROCESSING_HOOKS": [
        "drf_spectacular.hooks.postprocess_schema_enums",
        "octonomy.openapi.schema.add_namespace_parameters",
    ],
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

# Namespace rollout control plane (S7, issue #45). Env-backed Django settings; a
# toggle takes effect on restart/redeploy, so rollback latency == deploy latency.
# A Django system check (octonomy.core.checks) enforces the dependency contract
# between these so an invalid combination — notably v2 accepting namespaced writes
# that no read path can return — refuses to boot. Rollout enables SCHEMA -> READ ->
# AUTH -> V2_API -> WRITE; rollback disables V2_API -> AUTH -> WRITE -> READ
# (columns/SCHEMA stay). See docs/operations.md "Namespace Rollout & Operations".
#
# The read/auth machinery shipped in S2-S6 is always fail-closed; SCHEMA/READ/AUTH
# are rollout-phase assertions the system check orders. NAMESPACE_V2_API_ENABLED is
# the one flag that gates the edge: when off, namespaced /api/v2 requests are
# refused (the first rollback step) while global v1/v2 traffic continues.
NAMESPACE_SCHEMA_ENABLED = env_bool("OCTONOMY_NAMESPACE_SCHEMA_ENABLED", True)
NAMESPACE_READ_ENABLED = env_bool("OCTONOMY_NAMESPACE_READ_ENABLED", True)
NAMESPACE_AUTH_ENFORCED = env_bool("OCTONOMY_NAMESPACE_AUTH_ENFORCED", True)
NAMESPACE_V2_API_ENABLED = env_bool("OCTONOMY_NAMESPACE_V2_API_ENABLED", True)

# Kill-switch for namespaced (merchant/sub-tenant) writes. Defaults off and flips
# LAST in the rollout: persisting namespaced rows stays disabled until reads, auth,
# metrics, and the system check are all in place. While off, writes carrying a
# namespace scope are rejected on every path (HTTP and service layer); global
# writes (v1 and v2-global) are unaffected.
#
# Parsed strictly (only the literal "true" enables it), NOT via env_bool: this flag
# predates S7, so broadening its truthy set to include "1"/"yes"/"on" could silently
# enable namespaced writes on upgrade for a deployment already using one of those
# values. The kill-switch must never activate implicitly.
NAMESPACE_WRITE_ENABLED = os.getenv("OCTONOMY_NAMESPACE_WRITE_ENABLED", "false").lower() == "true"

OUTBOX_TRANSPORT = os.getenv("OCTONOMY_OUTBOX_TRANSPORT", "logging")
OUTBOX_WEBHOOK_URL = os.getenv("OCTONOMY_WEBHOOK_URL", "")
OUTBOX_WEBHOOK_SIGNING_SECRET = os.getenv("OCTONOMY_WEBHOOK_SIGNING_SECRET", "")
OUTBOX_WEBHOOK_TIMEOUT_SECONDS = env_int("OCTONOMY_WEBHOOK_TIMEOUT_SECONDS", 10)
OUTBOX_MAX_ATTEMPTS = env_int("OCTONOMY_OUTBOX_MAX_ATTEMPTS", 5)
OUTBOX_RETRY_BASE_SECONDS = env_int("OCTONOMY_OUTBOX_RETRY_BASE_SECONDS", 30)
OUTBOX_RETRY_MAX_SECONDS = env_int("OCTONOMY_OUTBOX_RETRY_MAX_SECONDS", 3600)
OUTBOX_CLAIM_TIMEOUT_SECONDS = env_int("OCTONOMY_OUTBOX_CLAIM_TIMEOUT_SECONDS", 60)

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
