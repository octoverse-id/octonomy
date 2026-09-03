"""Docs-page wiring: the multi-version Swagger UI, and a Redoc with no third party.

The single Swagger page carries a v1/v2 "Select a definition" dropdown. Its two
schema URLs are reversed against the request so they include any WSGI script-name
prefix -- matching NamespaceURLPathVersioning, which uses ``request.path_info`` to
stay correct under a prefix. A static ``SWAGGER_UI_SETTINGS`` string cannot do this
because drf-spectacular renders it once, with no request in hand.

Both pages serve every asset from this deployment (#146). That took three separate
things, because each lives in a different layer and fixing one does not touch the
others:

* ``SPECTACULAR_SETTINGS`` points the bundles at ``drf_spectacular_sidecar`` (HTML).
* The Redoc *template* is overridden here, because relocating the bundle does not
  remove the Google Fonts links the shipped template carries in its head (HTML).
* A ``Content-Security-Policy`` on both pages, because a self-hosted bundle still asks
  for whatever its own JavaScript asks for — and both of these bundles ask for
  something. Nothing that inspects the served HTML can see that class of request.
"""

from __future__ import annotations

import json
from urllib.parse import urlsplit

from django.conf import settings
from drf_spectacular.plumbing import get_relative_url
from drf_spectacular.utils import extend_schema
from drf_spectacular.views import SpectacularRedocView, SpectacularSwaggerView
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
        # Kills the "online validator" badge, which is the one part of Swagger UI that
        # calls out on its own. Left unset, swagger-ui defaults validatorUrl to
        # https://validator.swagger.io/validator and the badge renders an <img> at that
        # host with THIS deployment's absolute schema URL as a query parameter — so
        # swagger.io is told the hostname of every Octonomy an operator opens the docs
        # on, and an air-gapped install gets a broken image. Self-hosting the bundle
        # (#146) does not touch this: the URL lives inside the JavaScript, not the HTML,
        # which is also why the no-external-origin tests cannot see it.
        '"validatorUrl": null, '
        f'"urls": [{{"url": {v1}, "name": "v1"}}, {{"url": {v2}, "name": "v2"}}], '
        '"urls.primaryName": "v2"'
        "}"
    )


def _static_origin() -> str:
    """The scheme://host of ``STATIC_URL`` when it points off-origin, else "".

    ``OCTONOMY_STATIC_URL`` may legitimately be a full URL — fronting ``/static/`` with a
    CDN is a supported topology (``config/settings.py``). A policy hard-coded to ``'self'``
    would then block the deployment's own assets and leave the docs pages blank, so the
    origin is read from the setting rather than assumed. Read per response, not at import:
    a settings override has to move the policy with it.
    """

    static_url = getattr(settings, "STATIC_URL", "") or ""
    if not static_url.startswith(("http://", "https://")):
        return ""
    parts = urlsplit(static_url)
    return f"{parts.scheme}://{parts.netloc}"


def _docs_csp() -> str:
    """The egress policy for the docs pages.

    This is an EGRESS control, not an XSS one — worth being explicit about, because
    ``'unsafe-inline'`` in a CSP normally means the opposite. Both scripts and styles here
    are inline by construction (drf-spectacular inlines the Swagger init script and the
    ``Redoc.init`` call; Redoc injects styled-components rules at runtime), so requiring
    nonces would mean forking two upstream templates for no gain against a threat this
    page does not have — it renders a schema this app generated, with no user content.

    What it does buy is the guarantee #146 is actually about, enforced rather than
    documented: a bundle cannot reach a third party even when its own JavaScript tries.
    Two do try. Swagger UI's online-validator badge is disabled in settings above, but
    Redoc's sidebar attribution logo (``https://cdn.redoc.ly/redoc/logo-mini.svg``) has no
    configuration switch at all — ``img-src`` is what stops it, and Redoc drops the image
    when the request fails, so the attribution link still renders as text. It is also the
    only control that covers what an upstream bump ADDS — nothing in the served HTML, and
    so no test reading that HTML, can see a URL that lives inside a bundle.

    ``worker-src``/``child-src`` allow ``blob:`` because Redoc builds its search index in
    a worker created from a Blob URL; without it search silently stops working. ``child-src``
    is the Safari-before-15.4 fallback for the same thing.
    """

    static = _static_origin()
    assets = f"'self' {static}" if static else "'self'"
    return "; ".join(
        [
            "default-src 'self'",
            "base-uri 'self'",
            "form-action 'self'",
            f"script-src {assets} 'unsafe-inline'",
            f"style-src {assets} 'unsafe-inline'",
            f"img-src {assets} data:",
            f"font-src {assets} data:",
            "connect-src 'self'",
            "worker-src 'self' blob:",
            "child-src blob:",
        ]
    )


class SelfContainedDocsMixin:
    """Stamps the egress policy on a docs response.

    On ``finalize_response`` rather than in each ``get()``: ``VersionedSwaggerView``
    already overrides ``get()`` to rewrite the settings blob, and the two concerns should
    not have to know about each other. Assigned, not defaulted — if a proxy sets its own
    CSP too, browsers enforce every policy present, so the stricter of the two wins and
    an operator adding one cannot accidentally relax this.
    """

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Content-Security-Policy"] = _docs_csp()
        return response


class VersionedSwaggerView(SelfContainedDocsMixin, SpectacularSwaggerView):
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


class SelfHostedRedocView(SelfContainedDocsMixin, SpectacularRedocView):
    """Redoc UI that makes no request to a third-party host.

    ``REDOC_DIST = "SIDECAR"`` moves the Redoc bundle itself into this deployment's
    static, but the shipped ``drf_spectacular/redoc.html`` also preconnects to
    fonts.googleapis.com and loads a stylesheet from it — so the default page still
    reaches the internet, and still tells Google when an operator opens the docs. The
    override template drops exactly those links and inherits everything else.

    The mixin covers the half the template cannot: Redoc's own bundle requests its
    Redocly attribution logo from a CDN, and no Redoc setting turns that off.
    """

    template_name = "openapi/redoc.html"
