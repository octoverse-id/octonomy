"""Django settings for Octonomy."""

from __future__ import annotations

import os
import warnings
from pathlib import Path
from urllib.parse import urlparse

import dj_database_url
from django.core.exceptions import ImproperlyConfigured
from django.urls import reverse_lazy
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
API_VERSION = os.getenv("OCTONOMY_API_VERSION", "3.1.1")
ALLOWED_HOSTS = [
    host.strip() for host in os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
]

# This guard tests two things and nothing more: the value is non-empty, and it is not the
# local-dev literal. It does NOT judge strength, so `thisisthedjangomostsecretkey` boots,
# and so does a whitespace-only " " (which is truthy). That is deliberate as of #147/#148:
# the documentation was narrowed to describe this behaviour rather than the guard widened,
# because a length rule would break the short secret fixtures across CI and the test
# suite (inventory in TODOS.md CFG-2), and could refuse to start a
# deployment already running a short but random secret. `security.W009` reports some weak
# SECRET_KEY shapes, but it is a warning and runs only under `manage.py check --deploy`,
# which the container entrypoint never invokes.
# gstack-shortcut(dec-584a8f2c): no secret-strength check — upgrade when this guard is next
#   edited for any reason, or at the 3.2.0 release. See TODOS.md CFG-2.
# gstack-shortcut(dec-f77c11e8): whitespace-only values are accepted — same fix, same PR.
if not DEBUG and (not SECRET_KEY or SECRET_KEY == "local-dev-secret"):
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
#
# WhiteNoise sits at index 1, directly after SecurityMiddleware, as WhiteNoise documents:
# SecurityMiddleware's redirects and headers still apply, and a /static/ hit then
# short-circuits the rest of the chain. It is UNCONDITIONAL — not gated on ADMIN_ENABLED
# — because DEFAULT_RENDERER_CLASSES includes BrowsableAPIRenderer, so
# /static/rest_framework/* is needed by a non-optional surface. See "Static files" below.
#
# Deliberate trade-off in that placement: a static response never reaches
# RequestContextMiddleware (last), so it emits no `octonomy.requests` log line — which
# drops roughly 25 log lines per admin page load. Putting WhiteNoise last instead would
# run session/CSRF/auth per asset and risk `Vary: Cookie` or a CSRF cookie on static
# responses, so the lost log lines are the cheaper cost.
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
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

# --- Static files (bundled, app-served) --------------------------------------------
# Octonomy serves its own bundled static assets, on every channel, via the WhiteNoise
# middleware above.
#
# This REVERSES the original "no WhiteNoise — operators collect STATIC_ROOT and serve it
# externally" posture (#142/#143). Two reasons it had to go. It was unactionable on the
# container channels: the assets are baked into the image with no volume, no export
# step, a non-root user and a read-only root filesystem, so nothing could serve them and
# every /static/* request 404'd once DEBUG=false. And it was never admin-only — the DRF
# browsable API is on by default, so /static/rest_framework/* is required even when the
# admin console is off.
#
# Boundary: first-party BUNDLED assets only (django.contrib.admin, unfold, DRF).
# Octonomy stores no user uploads and defines no MEDIA_ROOT; WhiteNoise must never be
# pointed at user-supplied files.
#
# All three channels reach WhiteNoise by default. The systemd/VPS channel used to be an
# exception — nginx-octonomy.conf shipped a `location /static/` alias that answered before
# the request reached Gunicorn — but #145 removed it, because one nginx `expires` value
# cannot serve both hashed and unhashed filenames safely. An operator who re-adds such a
# block, or fronts STATIC_URL with a CDN, still wins over the app and takes the compression,
# CORS and cache headers below with it.
#
# STATIC_URL is ABSOLUTE and env-overridable, and both halves of that matter under a
# subpath deployment (the app mounted at /octonomy rather than /).
#
# Django script-prefixes a RELATIVE STATIC_URL — but only on first read, and it caches
# the result for the life of the process (LazySettings.__getattr__). Which prefix gets
# baked in therefore depends on what reads it first. Adding WhiteNoise moved that moment
# earlier: its startup index calls staticfiles_storage.url() per file to decide
# immutability, so the value now freezes while the middleware chain is being built, when
# the script prefix is still "/". Before this change the first read happened inside a
# request — but even then it was a race, not a feature: Kubernetes liveness and readiness
# probes hit the pod directly with no prefix, so whichever request arrived first decided
# the answer for every page that worker ever rendered.
#
# So rather than restore a coin flip, make it explicit. Absolute means Django leaves it
# alone and the value is exactly what it says.
#
# The subpath recipe, verified end to end and locked by
# tests/admin/test_static_serving.py::test_subpath_deployment_serves_and_links_static:
# set BOTH OCTONOMY_STATIC_URL=/octonomy/static/ and OCTONOMY_FORCE_SCRIPT_NAME=/octonomy.
# Templates then emit /octonomy/static/..., which the proxy routes; WhiteNoise strips
# FORCE_SCRIPT_NAME from its own prefix, so it still matches the /static/... path_info the
# WSGI server hands it.
#
# They are a pair. Setting FORCE_SCRIPT_NAME alone leaves templates linking /static/...
# while the app lives at /octonomy/, so a proxy routing only /octonomy/* never sees those
# requests — octonomy.W003 warns about that shape. It warns rather than refuses because
# whether it actually breaks depends on proxy routing this process cannot see: an operator
# who deliberately routes /static/ at the host root to this app is fine.
STATIC_URL = os.getenv("OCTONOMY_STATIC_URL", "/static/")
STATIC_ROOT = BASE_DIR / "staticfiles"

# A relative value would put back exactly the first-read caching nondeterminism the
# absolute default exists to remove, and it would do so silently. A full http(s) URL is
# allowed: pointing STATIC_URL at a CDN is a legitimate topology (and the one that needs
# WHITENOISE_ALLOW_ALL_ORIGINS turned back on, below).
if not STATIC_URL.startswith(("/", "http://", "https://")):
    raise ImproperlyConfigured(
        "OCTONOMY_STATIC_URL must be root-absolute (e.g. /octonomy/static/) or a full "
        f"http(s) URL; got {STATIC_URL!r}."
    )

# Off by default (Django's own default is None). Only a subpath deployment needs it, and
# then only together with OCTONOMY_STATIC_URL above; see the recipe there. It is what lets
# reverse() and {% static %} emit the prefix without depending on a per-request
# SCRIPT_NAME that the health probes do not carry.
#
# Validated, and not merely for tidiness. Django prepends this to every reverse() result
# verbatim, so a value carrying a scheme or a host turns every generated link absolute and
# off-site: OCTONOMY_FORCE_SCRIPT_NAME=https://evil.example renders the admin login form
# with action="https://evil.example/admin/login/", i.e. it posts an operator's password to
# someone else. This is process configuration rather than request input, so it is a typo
# and mis-provisioning guard rather than a defence against an attacker — but refusing to
# boot is plainly better than serving that page.
FORCE_SCRIPT_NAME = os.getenv("OCTONOMY_FORCE_SCRIPT_NAME") or None
if FORCE_SCRIPT_NAME is not None:
    _script_name = urlparse(FORCE_SCRIPT_NAME)
    # netloc catches the protocol-relative "//evil.example", which starts with "/" and so
    # would otherwise slip past the prefix test.
    if (
        not FORCE_SCRIPT_NAME.startswith("/")
        or _script_name.scheme
        or _script_name.netloc
        or _script_name.query
        or _script_name.fragment
    ):
        raise ImproperlyConfigured(
            "OCTONOMY_FORCE_SCRIPT_NAME must be a local absolute path with no scheme, "
            f"host, query or fragment (e.g. /octonomy); got {FORCE_SCRIPT_NAME!r}."
        )

# Hashed + compressed static. Content-addressed filenames mean a browser or CDN holding
# an asset from an earlier release cannot serve those stale bytes under the new one at
# the same URL — the reason this backend is preferred over plain compressed storage on
# the Docker and Kubernetes channels, where upgrades are frequent and unattended.
#
# Footgun: BOTH keys must be declared. Django does not merge settings.STORAGES with
# global_settings, so naming only "staticfiles" raises InvalidStorageError
# ("Could not find config for 'default' in settings.STORAGES").
#
# Manifest storage is strict: {% static %} raises at render time for any asset missing
# from staticfiles.json, so a deploy that skipped collectstatic fails loudly with a 500
# instead of quietly rendering unstyled. That is intended. octonomy.W002 catches the
# common shapes of it at boot — absent, empty, unreadable or manifest-less STATIC_ROOT —
# but it is a readiness heuristic, not a guarantee: it is gated on ADMIN_ENABLED and
# cannot see a populated-but-stale root. Most tests run the plain, non-manifest backend;
# see config/settings_pytest.py for that divergence and what still covers this one.
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

# Set explicitly rather than inherited: WhiteNoise defaults this to True, which stamps
# `Access-Control-Allow-Origin: *` onto every static response. In the shipped topology the
# app serves both the HTML and its assets, so every request is same-origin and the
# wildcard buys nothing. Declaring it keeps the header contract visible.
#
# The one topology that needs the default back: fronting /static/ with a CDN on a
# DIFFERENT origin than the admin. Unfold's Inter and Material Symbols faces are local
# @font-face files, and a browser will not use a cross-origin font without a permissive
# CORS header — so such a deployment must re-enable this (or set the header at the edge).
WHITENOISE_ALLOW_ALL_ORIGINS = False

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
    # Dropdown revealed by clicking the site header (top-left): quick links out to the
    # source repo and the two API doc surfaces. All open in a new tab (rel=noopener to
    # avoid tab-nabbing) so the admin session stays put. reverse_lazy honors a WSGI
    # SCRIPT_NAME subpath the same way SITE_URL does; the docs URL names live in the
    # always-on urlpatterns (not gated by ADMIN_ENABLED), so they resolve whenever the
    # dropdown renders.
    "SITE_DROPDOWN": [
        {
            "icon": "code",
            "title": "GitHub repository",
            "link": "https://github.com/octoverse-id/octonomy",
            "attrs": {"target": "_blank", "rel": "noopener noreferrer"},
        },
        {
            "icon": "api",
            "title": "Swagger API docs",
            "link": reverse_lazy("swagger-ui"),
            "attrs": {"target": "_blank", "rel": "noopener noreferrer"},
        },
        {
            "icon": "description",
            "title": "ReDoc API docs",
            "link": reverse_lazy("redoc"),
            "attrs": {"target": "_blank", "rel": "noopener noreferrer"},
        },
    ],
    "SHOW_HISTORY": True,
    "SHOW_VIEW_ON_SITE": True,
    # Group the sidebar by domain instead of Django's default per-app list. Icons are
    # Material Symbols names (https://fonts.google.com/icons). Links use reverse_lazy so
    # they resolve at render time (the admin is only mounted when ADMIN_ENABLED, which is
    # exactly when the sidebar renders). No per-item "permission" is set because the whole
    # site is already active-superuser-only (OctonomyAdminSite.has_permission).
    #
    # show_all_applications is off, so the sidebar shows ONLY this curated navigation —
    # every registered model MUST appear below or it becomes unreachable from the menu
    # (there is a test that fails if a model is dropped). Diagnostics/Access are
    # collapsible to keep the read-only and auth sections tucked away by default.
    "SIDEBAR": {
        "show_search": True,
        "show_all_applications": False,
        "navigation": [
            {
                "title": "Taxonomy",
                "collapsible": False,
                "items": [
                    {
                        "title": "Vocabularies",
                        "icon": "menu_book",
                        "link": reverse_lazy("admin:tags_vocabulary_changelist"),
                    },
                    {
                        "title": "Tags",
                        "icon": "sell",
                        "link": reverse_lazy("admin:tags_tag_changelist"),
                    },
                    {
                        "title": "Tag aliases",
                        "icon": "label",
                        "link": reverse_lazy("admin:tags_tagalias_changelist"),
                    },
                    {
                        "title": "Tag assignments",
                        "icon": "link",
                        "link": reverse_lazy("admin:assignments_tagassignment_changelist"),
                    },
                ],
            },
            {
                "title": "Diagnostics",
                "separator": True,
                "collapsible": True,
                "items": [
                    {
                        "title": "Audit logs",
                        "icon": "history",
                        "link": reverse_lazy("admin:audit_auditlog_changelist"),
                    },
                    {
                        "title": "Outbox events",
                        "icon": "outbox",
                        "link": reverse_lazy("admin:events_outboxevent_changelist"),
                    },
                    {
                        "title": "Service clients",
                        "icon": "key",
                        "link": reverse_lazy("admin:service_auth_serviceclient_changelist"),
                    },
                    {
                        "title": "Service client grants",
                        "icon": "verified_user",
                        "link": reverse_lazy("admin:service_auth_serviceclientgrant_changelist"),
                    },
                ],
            },
            {
                "title": "Access",
                "separator": True,
                "collapsible": True,
                "items": [
                    {
                        "title": "Users",
                        "icon": "account_circle",
                        "link": reverse_lazy("admin:auth_user_changelist"),
                    },
                    {
                        "title": "Groups",
                        "icon": "group",
                        "link": reverse_lazy("admin:auth_group_changelist"),
                    },
                ],
            },
        ],
    },
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
# Same two tests as the DJANGO_SECRET_KEY guard above, and the same deliberate limits —
# see the note there. The pepper has even less coverage: `security.W009` applies to
# SECRET_KEY alone, so beyond this empty/default test nothing inspects SERVICE_TOKEN_PEPPER
# anywhere, at boot or under `check --deploy`.
# gstack-shortcut(dec-584a8f2c): no secret-strength check — upgrade when this guard is next
#   edited for any reason, or at the 3.2.0 release. See TODOS.md CFG-2.
# gstack-shortcut(dec-f77c11e8): whitespace-only values are accepted — same fix, same PR.
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
