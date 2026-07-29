"""Swagger UI wiring for the multi-version docs.

The single Swagger page carries a v1/v2 "Select a definition" dropdown. Its two
schema URLs are reversed against the request so they include any WSGI script-name
prefix -- matching NamespaceURLPathVersioning, which uses ``request.path_info`` to
stay correct under a prefix. A static ``SWAGGER_UI_SETTINGS`` string cannot do this
because drf-spectacular renders it once, with no request in hand.
"""

from __future__ import annotations

import json

from drf_spectacular.plumbing import get_relative_url
from drf_spectacular.utils import extend_schema
from drf_spectacular.views import SpectacularSwaggerView
from rest_framework.reverse import reverse


def _swagger_ui_settings(request) -> str:
    # Raw JS string (drf-spectacular returns str settings verbatim) so it can
    # reference SwaggerUIStandalonePreset: the dropdown's topbar only renders under
    # StandaloneLayout, which that preset registers. json.dumps quotes each reversed
    # URL as a JS string literal; the URLs carry the script-name prefix, if any.
    # v1 reverses schema-v1, not schema: the default "schema" route now serves v2,
    # so labelling it "v1" would load the v2 document under the v1 tab. primaryName
    # is "v2" so the page opens on the advertised v2 definition.
    v1 = json.dumps(get_relative_url(reverse("schema-v1", request=request)))
    v2 = json.dumps(get_relative_url(reverse("schema-v2", request=request)))
    return (
        "{"
        '"deepLinking": true, '
        '"persistAuthorization": true, '
        '"displayOperationId": true, '
        '"layout": "StandaloneLayout", '
        '"presets": [SwaggerUIBundle.presets.apis, SwaggerUIStandalonePreset], '
        f'"urls": [{{"url": {v1}, "name": "v1"}}, {{"url": {v2}, "name": "v2"}}], '
        '"urls.primaryName": "v2"'
        "}"
    )


class VersionedSwaggerView(SpectacularSwaggerView):
    """Swagger UI whose version dropdown lists the v1 and v2 schema endpoints."""

    # Re-declare exclude: overriding get() drops the parent decorator, which would
    # otherwise leak /api/docs/swagger/ into the generated schema.
    @extend_schema(exclude=True)
    def get(self, request, *args, **kwargs):
        response = super().get(request, *args, **kwargs)
        # Mutate before returning: DRF renders the template in finalize_response,
        # after the handler returns, so the overridden settings take effect.
        response.data["settings"] = _swagger_ui_settings(request)
        return response
