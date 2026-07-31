"""Django settings for Octonomy."""

from __future__ import annotations

import os
import warnings
from pathlib import Path

import dj_database_url
from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# Load the project-root .env before any os.getenv() call below. settings.py is
# imported by every entry point (manage.py runserver, config.wsgi for gunicorn,
# config.asgi for uvicorn, and pytest), so loading here applies .env once, first,
# everywhere. Real process env vars take precedence — load_dotenv() does not
# override variables already set, so Docker/production values win over the file,
# and a missing .env (e.g. in prod) is a silent no-op.
load_dotenv(BASE_DIR / ".env")


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
API_VERSION = os.getenv("OCTONOMY_API_VERSION", "2.0.0")
ALLOWED_HOSTS = [
    host.strip() for host in os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
]

if not DEBUG and SECRET_KEY == "local-dev-secret":
    raise ImproperlyConfigured(
        "DJANGO_SECRET_KEY must be set to a non-default value when DEBUG is False."
    )

INSTALLED_APPS = [
    # Unfold must precede the admin app so its template overrides win (APP_DIRS
    # resolves in INSTALLED_APPS order). BasicAppConfig — not unfold's default app
    # config — because the default one swaps admin.site for a bare UnfoldAdminSite
    # and would drop OctonomyAdminSite's superuser-only gate.
    "unfold.apps.BasicAppConfig",
    # Replaces "django.contrib.admin": installs OctonomyAdminSite as the default
    # admin site (config/admin.py). The optional operator console is mounted at
    # /admin/ only when ADMIN_ENABLED (config/urls.py).
    "config.admin.OctonomyAdminConfig",
    # octonomy.core hosts a createsuperuser override that enforces the password
    # validators on the non-interactive bootstrap path (--noinput, which Django itself
    # does not validate). Management-command overrides resolve to the app listed
    # EARLIEST in INSTALLED_APPS, so core MUST precede django.contrib.auth (which ships
    # the stock command). core is abstract-only (no models/migrations), so its earlier
    # position is otherwise inert. Locked by tests/admin/test_createsuperuser.py.
    "octonomy.core",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.postgres",
    "rest_framework",
    "drf_spectacular",
    "octonomy.service_auth",
    "octonomy.audit",
    "octonomy.events",
    "octonomy.tags",
    "octonomy.assignments",
    "octonomy.openapi",
]

# Django-recommended order. Session/Csrf/Authentication/Message/XFrameOptions are
# required by the admin (and by admin.E408-E410 system checks); they do NOT affect
# REST: DEFAULT_AUTHENTICATION_CLASSES is empty so DRF never runs SessionAuthentication
# or its CSRF check, and APIView is csrf_exempt. RequestContextMiddleware stays last so
# request_id is available to admin writes too.
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
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
                # Required by django.contrib.admin (admin.E402/E404 system checks).
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
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

# --- Admin console (opt-in, superuser-only) --------------------------------------
# The django-unfold admin is an optional operator console over the headless REST
# service. It is mounted at /admin/ only when ADMIN_ENABLED is true (defaults to
# DEBUG); when disabled the route is absent (resolver 404, not a branded page). The
# site itself is superuser-only (config/admin.py). A deploy warning (octonomy.W001)
# fires when it is enabled with DEBUG=false. See docs/operations.md "Admin console".
ADMIN_ENABLED = env_bool("OCTONOMY_ADMIN_ENABLED", DEBUG)

# Static files back the admin's CSS/JS assets. No WhiteNoise / external service:
# operators run `manage.py collectstatic` and serve STATIC_ROOT themselves in prod.
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# Secure the session/CSRF cookies by default in production (DEBUG=false), while
# leaving an explicit env override for unusual deployments (e.g. TLS-terminating
# proxy on a private network).
SESSION_COOKIE_SECURE = env_bool("SESSION_COOKIE_SECURE", not DEBUG)
CSRF_COOKIE_SECURE = env_bool("CSRF_COOKIE_SECURE", not DEBUG)

# A direct login at /admin/login/ (no ?next=) otherwise falls back to Django's
# LOGIN_REDIRECT_URL default (/accounts/profile/), which has no route here → 404 after
# a successful login (the Unfold login template renders no hidden `next` field). The
# admin is the only login surface, so land successful logins on the admin index. Inert
# when the admin is disabled (no login view exists to trigger the redirect).
LOGIN_REDIRECT_URL = "admin:index"

# Behind a TLS-terminating proxy (HTTPS at the edge, HTTP to the app), Django sees
# request.scheme == "http": secure cookies are withheld and CSRF rejects the browser's
# https Origin, so admin login/writes 403. Opt in to trust the proxy's forwarded scheme
# — ONLY when the proxy sets X-Forwarded-Proto and strips any client-supplied value.
# Off by default: trusting a spoofable header would silently downgrade HTTPS
# enforcement. See docs/operations.md "Admin console".
if env_bool("OCTONOMY_TRUST_FORWARDED_PROTO", False):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# CSRF trusted origins for the admin's session/CSRF surface (the REST API is token-based
# and CSRF-exempt). Needed when the browser's admin origin differs from the host Django
# sees — e.g. a proxy/CDN in front of the admin. Comma-separated, scheme-qualified
# (https://admin.example.com). Empty by default; ALLOWED_HOSTS still governs Host.
CSRF_TRUSTED_ORIGINS = [
    origin.strip() for origin in os.getenv("CSRF_TRUSTED_ORIGINS", "").split(",") if origin.strip()
]

# Password strength validators for the admin's superuser accounts. The REST API is
# token-only, so before the admin these were irrelevant; the admin is the first
# password-authenticated surface, and a superuser has platform-wide access — so enforce
# Django's standard validators on createsuperuser and the Unfold user/password forms.
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Unfold branding. Kept intentionally minimal — no bespoke dashboard, JS build, or
# custom image assets. SITE_URL points the header/home affordance at the Swagger UI,
# reinforcing that REST is the primary surface; it is a dotted path to a request-aware
# callable (config.adminsite.admin_site_url) so the link honors a WSGI SCRIPT_NAME
# prefix under subpath deployments instead of hardcoding the host-root path.
UNFOLD = {
    "SITE_TITLE": "Octonomy Admin",
    "SITE_HEADER": "Octonomy Admin",
    "SITE_SUBHEADER": "Trusted development/operator interface — REST is the primary API.",
    "SITE_URL": "config.adminsite.admin_site_url",
    "SHOW_HISTORY": True,
    "SHOW_VIEW_ON_SITE": True,
}

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
    # The single Swagger UI with the v1/v2 version dropdown is wired in
    # octonomy.openapi.views.VersionedSwaggerView, which builds SWAGGER_UI_SETTINGS
    # per request so the dropdown's schema URLs carry any script-name prefix.
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
