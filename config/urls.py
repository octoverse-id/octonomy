"""URL routes for Octonomy."""

from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularRedocView, SpectacularSwaggerView

from octonomy.core.views import live, ready

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
# Swagger UI is a single page with a v1/v2 "Select a definition" dropdown, wired via
# SPECTACULAR_SETTINGS.SWAGGER_UI_SETTINGS. Redoc has no such dropdown, so it stays
# per-version.
urlpatterns = [
    path("health/live", live, name="health-live"),
    path("health/ready", ready, name="health-ready"),
    path("api/schema/", SpectacularAPIView.as_view(api_version="v1"), name="schema"),
    path("api/v2/schema/", SpectacularAPIView.as_view(api_version="v2"), name="schema-v2"),
    path("api/docs/swagger/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
    path("api/docs/redoc/", SpectacularRedocView.as_view(url_name="schema"), name="redoc"),
    path("api/docs/v2/redoc/", SpectacularRedocView.as_view(url_name="schema-v2"), name="redoc-v2"),
    path("api/<version>/", include(api_patterns)),
]
