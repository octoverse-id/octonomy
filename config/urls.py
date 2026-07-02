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

urlpatterns = [
    path("health/live", live, name="health-live"),
    path("health/ready", ready, name="health-ready"),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/swagger/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
    path("api/docs/redoc/", SpectacularRedocView.as_view(url_name="schema"), name="redoc"),
    path("api/<version>/", include(api_patterns)),
]
