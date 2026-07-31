"""URL routes for Octonomy."""

from django.conf import settings
from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularRedocView

from octonomy.core.views import live, ready
from octonomy.openapi.views import VersionedSwaggerView

# One view tree serves both API versions (the v1/v2 shim). URL-path versioning
# captures <version>; NamespaceURLPathVersioning validates it against
# ALLOWED_VERSIONS and resolves the request namespace scope.
api_patterns = [
    path("", include("octonomy.tags.urls")),
    path("", include("octonomy.assignments.urls")),
    path("", include("octonomy.audit.urls")),
]

# One schema endpoint per version backs the docs. Pinning api_version drives the
# generator directly (bypassing request.version, which the un-versioned /api/schema/
# path would otherwise resolve to DEFAULT_VERSION); the v2 schema's namespace
# parameters are added by octonomy.openapi.schema.add_namespace_parameters.
#
# v2 is the primary/advertised surface: the default "schema" route and the default
# "redoc" route serve v2, while v1 stays fully browsable via the explicit
# schema-v1 / redoc-v1 routes. v1 is supported, not deprecated.
#
# Swagger UI is a single page with a v1/v2 "Select a definition" dropdown, wired via
# VersionedSwaggerView. Redoc has no such dropdown, so it stays per-version.
urlpatterns = [
    path("health/live", live, name="health-live"),
    path("health/ready", ready, name="health-ready"),
    path("api/schema/", SpectacularAPIView.as_view(api_version="v2"), name="schema"),
    path("api/v1/schema/", SpectacularAPIView.as_view(api_version="v1"), name="schema-v1"),
    path("api/v2/schema/", SpectacularAPIView.as_view(api_version="v2"), name="schema-v2"),
    path("api/docs/swagger/", VersionedSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
    path("api/docs/redoc/", SpectacularRedocView.as_view(url_name="schema"), name="redoc"),
    path("api/docs/v1/redoc/", SpectacularRedocView.as_view(url_name="schema-v1"), name="redoc-v1"),
    path("api/docs/v2/redoc/", SpectacularRedocView.as_view(url_name="schema-v2"), name="redoc-v2"),
    path("api/<version>/", include(api_patterns)),
]

# The optional operator admin. Mounted only when ADMIN_ENABLED, and placed before
# the api/<version>/ catch-all above would ever matter (distinct prefix). When the
# flag is off the route is simply absent, so a request to /admin/ 404s at the
# resolver rather than rendering a branded denial page.
if settings.ADMIN_ENABLED:
    urlpatterns.insert(0, path("admin/", admin.site.urls))
