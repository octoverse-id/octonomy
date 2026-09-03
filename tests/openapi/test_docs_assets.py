"""The docs UI serves every byte it needs from this deployment (#146, epic #142).

Before this, `/api/docs/*` loaded Swagger UI and Redoc from
``cdn.jsdelivr.net/npm/...@latest`` — drf-spectacular's defaults, which
``config/settings.py`` did not override. Three problems, in ascending order of
seriousness: no air-gapped story, a UI version that drifts away from the
drf-spectacular that generated the schema, and unpinned third-party JavaScript
executing in an operator's browser while the image around it is digest-pinned,
SBOM'd and attested.

What these tests lock, and why each is separate:

* **No third-party origin in the rendered HTML.** The assertion is over *every* URL that
  leaves this origin, not the string ``cdn.jsdelivr.net``, because the concern is any
  outbound request. That is what caught the Google Fonts links in the shipped Redoc
  template, which ``REDOC_DIST = "SIDECAR"`` alone does not touch — see
  ``octonomy.openapi.views.SelfHostedRedocView``. It is also the guard that fires if
  a future drf-spectacular adds a CDN reference to a template we inherit.
* **The referenced assets are really served**, under the production manifest backend.
  Rewriting the URLs while the app 404s (or 500s) on them would satisfy the first
  half and leave the docs blank.
* **The v1/v2 dropdown still resolves both schemas.** Swagger's page is built by
  ``VersionedSwaggerView``; the asset change and the dropdown meet in the same
  rendered page, so they are asserted together here even though
  ``test_schema_routes.py`` owns the dropdown's own behaviour.
"""

from __future__ import annotations

import re

import pytest
from django.conf import settings
from django.core.management import call_command
from django.test import Client, override_settings

from config.settings import STORAGES as PRODUCTION_STORAGES

DOCS_PAGES = [
    "/api/docs/swagger/",
    "/api/docs/redoc/",
    "/api/docs/v1/redoc/",
    "/api/docs/v2/redoc/",
]

# Every src/href a docs page emits. Used to prove both halves: that none of them points
# off-site, and that all of them answer 200.
ASSET_REF = re.compile(r'(?:src|href)="([^"]+)"')

# Any http(s) URL, whatever the host, anywhere in the document — including inside the
# Swagger init script drf-spectacular inlines. Deliberately not a jsdelivr-specific
# pattern: the contract is "no third party", and the Redoc template reached Google, not
# jsdelivr.
#
# Scheme-only, on purpose. A `(?:https?:)?//` pattern would also match the `//` line
# comments in that inlined script, so protocol-relative URLs are checked where they can
# actually be fetched from instead — the src/href sweep below.
SCHEMED_URL = re.compile(r'https?://[^\s"\'<>)]+')


def render(path):
    response = Client().get(path)
    assert response.status_code == 200, f"{path} returned {response.status_code}"
    return response.content.decode()


@pytest.fixture(scope="module")
def _manifest_static_root(tmp_path_factory):
    """A STATIC_ROOT built by the real collectstatic under the production STORAGES.

    Manifest storage is the backend that matters here. It is what turns a docs asset
    that was never collected into a render-time 500 rather than a broken <script> tag,
    and it is what the shipped image and every deploy channel run — while the suite
    itself runs the plain backend (config/settings_pytest.py, dec-805139c7).

    Module-scoped because collectstatic costs a second or so and every test wants the
    same output; the override is released immediately so it cannot silently decide the
    backend for tests that did not ask for it.
    """

    root = tmp_path_factory.mktemp("staticfiles-docs")
    with override_settings(STATIC_ROOT=str(root), STORAGES=PRODUCTION_STORAGES):
        call_command("collectstatic", "--noinput", verbosity=0)
    return root


@pytest.fixture
def manifest_static(_manifest_static_root):
    with override_settings(STATIC_ROOT=str(_manifest_static_root), STORAGES=PRODUCTION_STORAGES):
        yield _manifest_static_root


# --- Nothing on these pages leaves the deployment -------------------------------------


@pytest.mark.parametrize("path", DOCS_PAGES)
def test_docs_page_references_no_external_origin(path):
    body = render(path)

    assert SCHEMED_URL.findall(body) == [], (
        f"{path} references an off-site URL; the docs UI must be self-contained"
    )


@pytest.mark.parametrize("path", DOCS_PAGES)
def test_docs_page_assets_are_all_local_paths(path):
    # The mirror of the assertion above, from the other direction: absence of an absolute
    # URL would also be satisfied by a page that references nothing at all, which is what
    # a mis-wired SIDECAR setting looks like. Every reference must be a root-absolute
    # local path, and there must be at least one.
    refs = ASSET_REF.findall(render(path))

    assert refs, f"{path} references no assets at all"
    for ref in refs:
        # `//host/path` is the case the scheme-only sweep above cannot see, and a browser
        # fetches it from that host exactly as it would an absolute URL.
        assert ref.startswith("/") and not ref.startswith("//"), (
            f"{path} references {ref!r}, which is not a local path"
        )


def test_swagger_page_pulls_its_bundle_from_the_sidecar_app():
    # Names the source explicitly. The test above would also pass if someone vendored
    # copies by hand into STATIC_ROOT, which would put the UI version back outside the
    # lockfile and the SBOM — the thing this issue set out to fix.
    body = render("/api/docs/swagger/")

    assert "/static/drf_spectacular_sidecar/swagger-ui-dist/swagger-ui.css" in body
    assert "/static/drf_spectacular_sidecar/swagger-ui-dist/swagger-ui-bundle.js" in body
    assert "/static/drf_spectacular_sidecar/swagger-ui-dist/swagger-ui-standalone-preset.js" in body
    # The favicon is a separate setting (SWAGGER_UI_FAVICON_HREF) and would otherwise
    # stay on the CDN by itself — still an outbound request every time the page loads.
    assert "/static/drf_spectacular_sidecar/swagger-ui-dist/favicon-32x32.png" in body


@pytest.mark.parametrize("path", ["/api/docs/redoc/", "/api/docs/v1/redoc/", "/api/docs/v2/redoc/"])
def test_redoc_pages_pull_their_bundle_from_the_sidecar_app(path):
    assert "/static/drf_spectacular_sidecar/redoc/bundles/redoc.standalone.js" in render(path)


@pytest.mark.parametrize("path", ["/api/docs/redoc/", "/api/docs/v1/redoc/", "/api/docs/v2/redoc/"])
def test_redoc_pages_do_not_load_google_fonts(path):
    """The specific regression the override template exists for.

    ``REDOC_DIST = "SIDECAR"`` relocates the bundle and nothing else: the shipped
    ``drf_spectacular/redoc.html`` still preconnects to fonts.googleapis.com and
    fonts.gstatic.com and loads a stylesheet from Google. Covered by the
    no-absolute-URL test above too, but named separately so a failure says what broke
    — most likely the override template no longer winning, or upstream renaming the
    ``head`` block it overrides.
    """

    body = render(path)

    assert "fonts.googleapis.com" not in body
    assert "fonts.gstatic.com" not in body


# --- The assets are actually there ----------------------------------------------------


@pytest.mark.parametrize("path", DOCS_PAGES)
def test_every_asset_a_docs_page_references_is_served(manifest_static, path):
    """Renders under the production backend, then fetches what the page asked for.

    Both halves matter and neither implies the other. Under manifest storage the
    render itself fails with ``Missing staticfiles manifest entry`` if an asset was
    never collected — that is the 500 an operator would hit — and a page that renders
    can still reference a path WhiteNoise does not serve.

    The URLs are taken from the rendered HTML rather than hardcoded, so this follows
    the hashed names the manifest actually produced instead of the unhashed originals
    (collectstatic writes both, so probing an original proves nothing about the
    manifest).
    """

    refs = ASSET_REF.findall(render(path))
    assert refs

    for ref in refs:
        response = Client().get(ref)
        assert response.status_code == 200, (
            f"{path} references {ref}, which returned {response.status_code}"
        )
        assert response.getvalue(), f"{ref} served an empty body"


def test_docs_assets_are_content_addressed_under_manifest_storage(manifest_static):
    # Proves the previous test ran against the manifest backend rather than passing
    # through plain storage, where the unhashed originals answer 200 regardless.
    refs = ASSET_REF.findall(render("/api/docs/swagger/"))

    assert all(re.search(r"\.[0-9a-f]{8,}\.", ref) for ref in refs), (
        f"expected hashed asset URLs under the production backend, got {refs}"
    )


# --- The dropdown the assets sit next to ----------------------------------------------


def test_swagger_dropdown_still_resolves_both_schema_urls(manifest_static):
    """Self-hosting the bundle must not disturb VersionedSwaggerView's dropdown.

    ``VersionedSwaggerView`` overwrites ``response.data["settings"]`` after the parent
    view has built the rest of the context — including the three asset URLs. Asserting
    both in one render is what proves the two mechanisms coexist; the dropdown's own
    behaviour (which definition is primary, script-name prefixes) belongs to
    ``test_schema_routes.py``.

    Under manifest storage specifically, because that is where a missing sidecar entry
    raises during the render and would take the dropdown down with the page.
    """

    body = render("/api/docs/swagger/")

    assert '"url": "/api/v1/schema/", "name": "v1"' in body
    assert '"url": "/api/v2/schema/", "name": "v2"' in body
    assert "SwaggerUIStandalonePreset" in body
    assert "drf_spectacular_sidecar/swagger-ui-dist/swagger-ui-standalone-preset" in body


# --- The calls the HTML sweep cannot see -----------------------------------------------
#
# A self-hosted bundle still asks for whatever its own JavaScript asks for, and both of
# these bundles ask for something. Neither URL appears in the served HTML, so every
# assertion above passes with the requests live. These are the two controls that stop
# them, and they are the ones that survive an upstream bump adding a third.


@pytest.mark.parametrize("path", DOCS_PAGES)
def test_docs_pages_carry_the_egress_csp(path):
    """The enforced half of the "no third party" claim.

    Verified in a real browser against a DEBUG=false Gunicorn: Swagger renders its 29
    operations and the v1/v2 dropdown with an empty console, and Redoc renders its
    sidebar, search and every operation while
    ``https://cdn.redoc.ly/redoc/logo-mini.svg`` is refused with "violates the following
    Content Security Policy directive".
    """

    response = Client().get(path)

    assert response.status_code == 200
    policy = response.headers["Content-Security-Policy"]
    assert "default-src 'self'" in policy


def test_the_csp_directive_that_blocks_redocs_cdn_logo():
    """Named on its own because it is the directive doing the work.

    Redoc's sidebar attribution mounts an ``<img>`` pointing at cdn.redoc.ly and offers no
    setting to disable it. ``img-src`` is what refuses the request; Redoc's own onError
    handler then unmounts the image, so the attribution link stays as text and the page
    shows no broken-image icon (confirmed in-browser: ``document.images.length`` is 0).
    """

    policy = Client().get("/api/docs/redoc/").headers["Content-Security-Policy"]

    assert "img-src 'self' data:" in policy


def test_the_csp_allows_the_blob_worker_redoc_builds_its_search_index_in():
    """The directive whose absence would break a feature instead of a request.

    Redoc runs its search index in ``new Worker(URL.createObjectURL(new Blob([...])))``.
    Under a policy without ``blob:`` the worker never starts and search silently stops
    working — no error a page test would notice. ``child-src`` carries the same allowance
    for Safari before 15.4, which has no ``worker-src``.
    """

    policy = Client().get("/api/docs/redoc/").headers["Content-Security-Policy"]

    assert "worker-src 'self' blob:" in policy
    assert "child-src blob:" in policy


def test_the_csp_admits_an_off_origin_static_url():
    """A CDN in front of /static/ is a supported topology, and 'self' alone would break it.

    ``config/settings.py`` explicitly allows OCTONOMY_STATIC_URL to be a full http(s) URL.
    A policy hard-coded to ``'self'`` would then refuse the deployment's own bundles and
    leave the docs blank — a self-inflicted outage in the name of blocking third parties.
    """

    with override_settings(STATIC_URL="https://cdn.example.com/static/"):
        policy = Client().get("/api/docs/swagger/").headers["Content-Security-Policy"]

    assert "script-src 'self' https://cdn.example.com 'unsafe-inline'" in policy
    assert "img-src 'self' https://cdn.example.com data:" in policy
    # The origin only — a path in a CSP source would not match how browsers compare hosts.
    assert "https://cdn.example.com/static" not in policy


def test_the_csp_admits_a_protocol_relative_static_url():
    """The shape that slips past a naive ``startswith("https://")`` check.

    ``config/settings.py`` validates OCTONOMY_STATIC_URL as root-absolute OR a full http(s)
    URL — and ``//cdn.example.com/static/`` starts with "/", so it is accepted, and
    ``{% static %}`` then emits ``//cdn...`` asset URLs. A policy that recognised only the
    schemed form would emit ``'self'`` alone and block every bundle the deployment serves:
    a blank docs page caused by the control meant to protect it.

    The host is emitted BARE. ``//cdn.example.com`` matches no CSP source-expression, so a
    browser discards that token as invalid and the directive falls back to ``'self'`` —
    the same outage, but silent.
    """

    with override_settings(STATIC_URL="//cdn.example.com/static/"):
        policy = Client().get("/api/docs/swagger/").headers["Content-Security-Policy"]

    assert "script-src 'self' cdn.example.com 'unsafe-inline'" in policy
    assert "img-src 'self' cdn.example.com data:" in policy
    assert "//cdn.example.com" not in policy


def test_the_csp_admits_an_ascii_punycode_static_host():
    """The IDN topology that CAN be enforced, and the one operators should use.

    A host already written in punycode is an ordinary ASCII host: Chromium parses
    ``https://xn--fa-hia.de`` as a source without complaint, and it is exactly the host the
    browser resolves the asset URL to.
    """

    with override_settings(STATIC_URL="https://xn--fa-hia.de/static/"):
        policy = Client().get("/api/docs/swagger/").headers["Content-Security-Policy"]

    assert "script-src 'self' https://xn--fa-hia.de 'unsafe-inline'" in policy


@pytest.mark.parametrize(
    ("static_url", "why"),
    [
        # CSP's host-source grammar has no IPv6 form. Chromium says so out loud: "contains
        # an invalid source: 'http://[::1]:9000'. It will be ignored." Emitting it is not a
        # best effort — it is a policy that provably blocks the assets.
        ("http://[::1]:9000/static/", "IPv6 literal"),
        # Python's idna codec is IDNA 2003 and browsers are not: it maps faß.de to fass.de
        # where a browser fetches xn--fa-hia.de. Naming the wrong host blocks just as hard
        # as naming none. Such an operator can write STATIC_URL in punycode instead.
        ("https://faß.de/static/", "IDNA 2003 disagrees with the browser"),
        # urlsplit defers port validation to attribute access; this raises there.
        ("https://cdn.example.com:99999/static/", "unparseable port"),
        # urlsplit ends a netloc only at /?#, so a stray semicolon lands INSIDE it and would
        # otherwise be pasted into the header, adding a directive nobody wrote.
        ("https://cdn.example.com; script-src */static/", "would restructure the header"),
    ],
)
def test_no_policy_at_all_when_the_static_origin_cannot_be_expressed(static_url, why):
    """Fails OPEN, and that is the deliberate half of this design.

    Shipping the policy WITHOUT the origin is not a weaker guarantee — it is a broken
    deployment: every bundle blocked and a blank docs page, caused by the control meant to
    protect it. Omitting the header leaves such a deployment exactly where #146 found it,
    with the assets still self-hosted and the egress documented rather than enforced.

    All four shapes are exotic for a self-hosted service, all four are the operator's own
    topology rather than the shipped default, and all four have a fix the operator
    controls: write the origin as an ASCII host.
    """

    with override_settings(STATIC_URL=static_url):
        response = Client().get("/api/docs/swagger/")

    assert response.status_code == 200, why
    assert "Content-Security-Policy" not in response.headers, why


def test_the_csp_is_scoped_to_the_docs_pages():
    # The JSON API needs no policy and must not inherit one: `connect-src 'self'` on an API
    # response means nothing, but a `default-src` that someone later tightens would start
    # constraining consumers this app has no business constraining.
    assert "Content-Security-Policy" not in Client().get("/health/live").headers


def test_swagger_disables_the_online_validator_badge():
    """Swagger UI's one self-initiated third-party call, and it is not in the HTML.

    Left unset, ``validatorUrl`` defaults to ``https://validator.swagger.io/validator``
    and the badge renders an ``<img>`` at that host carrying this deployment's absolute
    schema URL as a query parameter. Self-hosting the bundle does not touch it: the URL
    is baked into ``swagger-ui-bundle.js``, so every no-external-origin assertion in this
    module passes with the badge live. Hence a settings-level guard.

    Two consequences it prevents: swagger.io learns the hostname of every Octonomy whose
    docs someone opens, and an air-gapped install renders a broken image on the page this
    issue exists to make work offline.
    """

    assert '"validatorUrl": null' in render("/api/docs/swagger/")


# --- Configuration locks ---------------------------------------------------------------


def test_all_three_dist_settings_point_at_the_sidecar():
    """Each key is independent; leaving one out leaves that asset on the CDN.

    Asserted against the settings rather than only through a rendered page so the
    failure names the missing key. ``SWAGGER_UI_FAVICON_HREF`` is the one that is easy
    to forget — it does not fall back to ``SWAGGER_UI_DIST``.
    """

    spectacular = settings.SPECTACULAR_SETTINGS

    assert spectacular["SWAGGER_UI_DIST"] == "SIDECAR"
    assert spectacular["SWAGGER_UI_FAVICON_HREF"] == "SIDECAR"
    assert spectacular["REDOC_DIST"] == "SIDECAR"


def test_the_sidecar_app_is_installed():
    # The settings above are inert without it: "SIDECAR" resolves through {% static %},
    # and with the app missing, collectstatic never gathers the files, so the docs pages
    # 500 under manifest storage instead of falling back to anything.
    assert "drf_spectacular_sidecar" in settings.INSTALLED_APPS
