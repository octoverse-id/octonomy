"""The docs UI serves every byte it needs from this deployment (#146, epic #142).

Before this, `/api/docs/*` loaded Swagger UI and Redoc from
``cdn.jsdelivr.net/npm/...@latest`` — drf-spectacular's defaults, which
``config/settings.py`` did not override. Three problems, in ascending order of
seriousness: no air-gapped story, a UI version that drifts away from the
drf-spectacular that generated the schema, and unpinned third-party JavaScript
executing in an operator's browser while the image around it is digest-pinned,
SBOM'd and attested.

What these tests lock, and why each is separate:

* **No third-party origin in the rendered HTML.** The assertion is over *every*
  absolute URL, not the string ``cdn.jsdelivr.net``, because the concern is any
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

# Any absolute URL, whatever the scheme or host. Deliberately not a jsdelivr-specific
# pattern: the contract is "no third party", and the Redoc template reached Google, not
# jsdelivr.
ABSOLUTE_URL = re.compile(r'(?:https?:)?//[^\s"\'<>)]+')


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

    assert ABSOLUTE_URL.findall(body) == [], (
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
