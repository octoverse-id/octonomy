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
import re
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


# Characters that would let a STATIC_URL restructure the header rather than name a host in
# it. Everything else about the host is left to _static_origin's expressibility test below;
# this pattern is not a hostname validator.
_HEADER_STRUCTURAL = re.compile(r"[\s;,'\"]")

# Sentinel for "this deployment's asset origin cannot be written as a CSP source". Distinct
# from "" (same-origin, nothing to add), because the two demand opposite responses: "" gets
# the normal policy, this one gets NO policy at all. See _docs_csp.
UNEXPRESSIBLE = object()


def _static_origin():
    """``STATIC_URL``'s origin as a CSP host-source: "" if same-origin, UNEXPRESSIBLE if not.

    ``OCTONOMY_STATIC_URL`` may legitimately be a full URL — fronting ``/static/`` with a
    CDN is a supported topology (``config/settings.py``) — so the origin has to reach the
    policy or the policy blocks the deployment's own bundles and the docs render blank.

    Keyed on the parsed host, not on an ``https://`` prefix, because the setting's own
    validator accepts a PROTOCOL-RELATIVE ``//cdn.example.com/static/``: it starts with "/",
    so it passes the root-absolute test, and ``{% static %}`` then emits ``//cdn...`` asset
    URLs. Such a host is emitted BARE (``cdn.example.com``); CSP makes the scheme optional
    and a scheme-less host matches the page's own scheme, while a leading ``//`` matches no
    source-expression at all. Verified in Chromium: ``cdn.example.com``,
    ``cdn.example.com:8443``, ``*.example.com`` and ``https://cdn.example.com:8443`` all
    parse without complaint.

    Two shapes are NOT expressible, and both are reported rather than approximated:

    * **An IPv6 literal.** CSP's ``host-source`` grammar has no IPv6 form. Chromium says so
      out loud — "contains an invalid source: 'http://[::1]:9000'. It will be ignored" —
      so emitting it is not a best-effort, it is a policy that provably blocks the assets.
    * **A non-ASCII host.** Python's built-in ``idna`` codec is IDNA 2003, which browsers
      are not: it maps ``faß.de`` to ``fass.de`` where a browser fetches from
      ``xn--fa-hia.de``. Naming the wrong host has the same effect as naming none. An
      operator on an IDN domain can write STATIC_URL in punycode and get full enforcement.

    A THIRD case is the dangerous one, because it does not look like a failure: a URL whose
    origin a browser reads and ``urlsplit`` does not. ``urlsplit`` follows RFC 3986;
    browsers follow WHATWG, which skips repeated slashes before an authority and treats a
    backslash as a slash. Measured in Chromium against a page on octonomy.example.com — all
    of these load from cdn.example.com, while ``urlsplit`` reports no netloc for any:

        ///cdn.example.com/static/x.js          https:///cdn.example.com/static/x.js
        ////cdn.example.com/static/x.js         https:////cdn.example.com/static/x.js
        /\cdn.example.com/static/x.js           /<TAB>/cdn.example.com/static/x.js

    Reading "no netloc" as "same-origin" would emit the ordinary ``'self'`` policy for those
    and block every asset the deployment serves. So same-origin has to be PROVEN rather than
    inferred from the absence of a host: one leading slash, and a second character that no
    parser can read as the start of an authority. Anything else with no host is unexpressible
    and gets no policy — a spelling a browser and Python disagree about is not one to guess
    at. The fix is the operator's and it is spelling: ``//cdn.example.com/static/`` and
    ``https://cdn.example.com/static/`` both keep full enforcement.

    Userinfo and the path drop out by construction: only host and port are read.

    Read per response, not at import: a settings override has to move the policy with it.
    """

    static_url = getattr(settings, "STATIC_URL", "") or ""
    # Tab, LF and CR are removed by URL parsers before anything else is decided, so they are
    # removed here too — otherwise "/<TAB>/cdn.example.com/" reads as a local path here and
    # as an authority in the browser, which is the disagreement this whole branch is about.
    probe = static_url.translate({0x09: None, 0x0A: None, 0x0D: None})
    parts = urlsplit(probe)
    try:
        host, port = parts.hostname, parts.port
    except ValueError:
        # urlsplit defers port validation to attribute access.
        return UNEXPRESSIBLE
    if not host:
        # Same-origin, but only when provably so — see the third case above.
        if probe.startswith("/") and probe[1:2] not in ("/", "\\"):
            return ""
        return UNEXPRESSIBLE
    if _HEADER_STRUCTURAL.search(host) or not host.isascii() or ":" in host:
        # ":" in a hostname means an IPv6 literal — urlsplit strips its brackets.
        return UNEXPRESSIBLE

    authority = f"{host}:{port}" if port else host
    return f"{parts.scheme}://{authority}" if parts.scheme else authority


def _docs_csp() -> str | None:
    """The egress policy for the docs pages, or None when it cannot be written honestly.

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

    **Fails OPEN.** When ``STATIC_URL``'s origin cannot be expressed as a CSP source, no
    header is sent at all. The alternative — ship the policy without that origin — is not a
    weaker guarantee, it is a broken deployment: every bundle blocked and a blank docs page,
    caused by the control meant to protect it. Omitting leaves such a deployment exactly
    where this issue found it (assets self-hosted, egress documented rather than enforced),
    which is a real but bounded loss, and it is the operator's own unusual topology rather
    than the shipped default. Both shapes are exotic for a self-hosted service, and both
    have a fix the operator controls: write the origin as an ASCII host.
    """

    static = _static_origin()
    if static is UNEXPRESSIBLE:
        return None
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
        policy = _docs_csp()
        if policy is not None:
            response["Content-Security-Policy"] = policy
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
