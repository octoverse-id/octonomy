"""The CI static-delivery assertion (#144, part of epic #142).

``scripts/assert-static-served.sh`` boots-and-probes the real image in CI. It is the only
control that catches an image whose production staticfiles manifest is missing or stale:
the pytest suite runs the plain backend (dec-805139c7), so that packaging failure passes
every other job. A gate carrying that weight has to be shown capable of failing, so each
case below stands up a stub server behaving like one specific way a deployment breaks.

The script is also driven against real containers before landing — deleting the WhiteNoise
middleware line and deleting collectstatic from the Dockerfile both make it fail. These
tests cover the script's own logic, which no container run can: they are what stops it
degrading into something that always passes.
"""

from __future__ import annotations

import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

HASHED_CSS = "/static/unfold/css/styles.0123456789ab.css"
HASHED_CSS_2 = "/static/unfold/css/fonts.1122334455aa.css"
HASHED_JS = "/static/unfold/js/app.abcdef012345.js"
HASHED_DRF_CSS = "/static/rest_framework/css/bootstrap.min.fedcba987654.css"

# Mirrors the real templates: several stylesheets, then scripts. The ORDER matters — with
# stylesheets first, a gate that sampled one URL per page never reached a .js asset at all.
ADMIN_HTML = (
    f'<html><head><link rel="stylesheet" href="{HASHED_CSS}">'
    f'<link rel="stylesheet" href="{HASHED_CSS_2}">'
    f'<script src="{HASHED_JS}"></script></head></html>'
)
DRF_HTML = f'<html><head><link rel="stylesheet" href="{HASHED_DRF_CSS}"></head></html>'

# The docs UI (#146). Its bundles live under /static/drf_spectacular_sidecar/, which the
# script asserts on by name: that prefix is what distinguishes "served from this
# deployment" from "fetched from jsDelivr", which is the whole point of the surface.
HASHED_SWAGGER_CSS = "/static/drf_spectacular_sidecar/swagger-ui-dist/swagger-ui.aabbccdd0011.css"
HASHED_SWAGGER_JS = (
    "/static/drf_spectacular_sidecar/swagger-ui-dist/swagger-ui-bundle.bbccddee1122.js"
)
HASHED_REDOC_JS = "/static/drf_spectacular_sidecar/redoc/bundles/redoc.standalone.ccddeeff2233.js"

# The inline <script> is not decoration. drf-spectacular inlines its Swagger init script
# into the page, and that script contains `//` line comments — so a single
# `(https?:)?//` pattern would flag every healthy docs page as protocol-relative. This
# fixture is what keeps the two-grep split honest.
SWAGGER_HTML = (
    f'<html><head><link rel="stylesheet" href="{HASHED_SWAGGER_CSS}"></head>'
    f'<body><script src="{HASHED_SWAGGER_JS}"></script>'
    "<script>\n// only retry once to prevent endless loop.\nconst ui = 1;\n</script>"
    "</body></html>"
)
REDOC_HTML = f'<html><head></head><body><script src="{HASHED_REDOC_JS}"></script></body></html>'

# The egress policy the docs views stamp on every response. The gate checks it because
# the HTML sweep cannot see a URL baked into a bundle, and both bundles carry one — Redoc
# fetches its Redocly attribution logo from a CDN with no setting to disable it.
DOCS_CSP = {
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' data:; worker-src 'self' blob:; child-src blob:"
    )
}

# What a non-manifest deployment renders. The script must reject it: the whole point is to
# probe the content-addressed path a browser really requests.
UNHASHED_HTML = (
    '<html><head><link rel="stylesheet" href="/static/admin/css/base.css"></head></html>'
)

CSS = 'text/css; charset="utf-8"'
JS = 'text/javascript; charset="utf-8"'


class _Stub(BaseHTTPRequestHandler):
    """Serves one scripted deployment shape."""

    protocol_version = "HTTP/1.1"

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler's required spelling
        status, ctype, body, headers, truncate = self.server.routes.get(
            self.path, self.server.default
        )
        payload = body.encode()
        self.send_response(status)
        if ctype:
            self.send_header("Content-Type", ctype)
        for key, value in headers.items():
            self.send_header(key, value)
        # Declaring more than is sent is how a real truncated transfer looks on the wire:
        # curl reports the status and content type, then fails part-way through the body.
        self.send_header("Content-Length", str(len(payload) * 2 if truncate else len(payload)))
        self.end_headers()
        self.wfile.write(payload)
        if truncate:
            self.close_connection = True

    def log_message(self, *args):
        """Silence the per-request stderr line; failures are asserted on, not read."""


@pytest.fixture
def stub_server():
    """Run a scripted HTTP server and yield its base URL."""

    servers = []

    def _start(routes, default=(404, "text/html", "not found", {}, False)):
        server = HTTPServer(("127.0.0.1", 0), _Stub)
        server.routes = routes
        server.default = default
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return f"http://127.0.0.1:{server.server_port}"

    yield _start

    for server in servers:
        server.shutdown()
        server.server_close()


def _healthy(**overrides):
    routes = {
        "/admin/login/": (200, "text/html", ADMIN_HTML, {}, False),
        "/api/v2/tags": (403, "text/html", DRF_HTML, {}, False),
        HASHED_CSS: (200, CSS, "body{}", {}, False),
        HASHED_CSS_2: (200, CSS, "body{}", {}, False),
        HASHED_JS: (200, JS, "1;", {}, False),
        HASHED_DRF_CSS: (200, CSS, "body{}", {}, False),
        "/api/docs/swagger/": (200, "text/html", SWAGGER_HTML, DOCS_CSP, False),
        "/api/docs/redoc/": (200, "text/html", REDOC_HTML, DOCS_CSP, False),
        HASHED_SWAGGER_CSS: (200, CSS, "body{}", {}, False),
        HASHED_SWAGGER_JS: (200, JS, "1;", {}, False),
        HASHED_REDOC_JS: (200, JS, "1;", {}, False),
    }
    routes.update(overrides)
    return routes


def test_a_correctly_serving_deployment_passes(run_script, stub_server):
    result = run_script("assert-static-served.sh", stub_server(_healthy()))

    assert result.returncode == 0, result.output
    assert "assert-static-served OK" in result.stdout


# --- The admin surface -------------------------------------------------------------------


def test_an_admin_page_that_500s_fails(run_script, stub_server):
    """The real shape of a broken manifest: a render-time 500, not a missing asset."""

    base = stub_server(_healthy(**{"/admin/login/": (500, "text/html", "Server Error", {}, False)}))

    result = run_script("assert-static-served.sh", base)

    assert result.returncode == 1
    assert "returned 500" in result.output


def test_an_admin_page_with_no_hashed_asset_fails(run_script, stub_server):
    """If nothing is content-addressed, manifest storage is not in effect and every
    remaining assertion would be checking the wrong backend."""

    base = stub_server(_healthy(**{"/admin/login/": (200, "text/html", UNHASHED_HTML, {}, False)}))

    result = run_script("assert-static-served.sh", base)

    assert result.returncode == 1
    assert "no hashed asset URL" in result.output


def test_a_hashed_admin_asset_that_404s_fails(run_script, stub_server):
    """What deleting the WhiteNoise middleware looks like: the page renders, its assets
    do not resolve."""

    base = stub_server(_healthy(**{HASHED_CSS: (404, "text/html", "nope", {}, False)}))

    result = run_script("assert-static-served.sh", base)

    assert result.returncode == 1
    assert "returned 404" in result.output


# --- The browsable API surface -----------------------------------------------------------
#
# DRF's renderer is in DEFAULT_RENDERER_CLASSES, so this is never an optional surface. Its
# URL is taken from the rendered page for the same reason the admin's is: probing the known
# original /static/rest_framework/css/bootstrap.min.css would return 200 off a file
# collectstatic wrote regardless of whether the manifest maps it, while the real page 500s.


def test_a_browsable_api_that_500s_fails(run_script, stub_server):
    base = stub_server(_healthy(**{"/api/v2/tags": (500, "text/html", "Server Error", {}, False)}))

    result = run_script("assert-static-served.sh", base)

    assert result.returncode == 1
    assert "browsable API returned 500" in result.output


def test_a_browsable_api_with_no_hashed_asset_fails(run_script, stub_server):
    base = stub_server(_healthy(**{"/api/v2/tags": (403, "text/html", UNHASHED_HTML, {}, False)}))

    result = run_script("assert-static-served.sh", base)

    assert result.returncode == 1
    assert "no hashed rest_framework asset" in result.output


def test_a_hashed_drf_asset_that_404s_fails(run_script, stub_server):
    """The case a hard-coded unhashed probe would have missed entirely."""

    base = stub_server(_healthy(**{HASHED_DRF_CSS: (404, "text/html", "nope", {}, False)}))

    result = run_script("assert-static-served.sh", base)

    assert result.returncode == 1
    assert "returned 404" in result.output


def test_a_404_on_the_probed_endpoint_fails(run_script, stub_server):
    """A moved route must not quietly stop the browsable-API half from being checked.

    It would fail anyway — Django's bare 404 page references no assets — but with a
    message about a missing hashed asset rather than about the missing route.
    """

    base = stub_server(
        _healthy(**{"/api/v2/tags": (404, "text/html", "<h1>Not Found</h1>", {}, False)})
    )

    result = run_script("assert-static-served.sh", base)

    assert result.returncode == 1
    assert "the route this gate probes has moved" in result.output


def test_a_denied_but_rendered_browsable_api_is_accepted(run_script, stub_server):
    """No token is sent, so DRF denies the request — by rendering the very template under
    test. A 4xx here is success; only a 5xx is the manifest failure."""

    base = stub_server(_healthy(**{"/api/v2/tags": (401, "text/html", DRF_HTML, {}, False)}))

    assert run_script("assert-static-served.sh", base).returncode == 0


# --- The docs UI surface -------------------------------------------------------------
#
# Added with #146, which moved the Swagger UI and Redoc bundles off cdn.jsdelivr.net@latest
# and into the image. Two distinct guarantees are asserted per page and both can regress
# on their own: the page renders and its assets resolve (the same manifest guarantee as
# the surfaces above), and the page references nothing off-box.


def test_a_docs_page_that_500s_fails(run_script, stub_server):
    """Under manifest storage an uncollected sidecar bundle is a 500 on the page.

    That is the shape of "drf_spectacular_sidecar is not in INSTALLED_APPS" or "the image
    was built without collectstatic" — not a 404 on the asset.
    """

    base = stub_server(
        _healthy(**{"/api/docs/swagger/": (500, "text/html", "Server Error", {}, False)})
    )

    result = run_script("assert-static-served.sh", base)

    assert result.returncode == 1
    assert "returned 500" in result.output


def test_a_docs_page_still_pointing_at_a_cdn_fails(run_script, stub_server):
    """The exact pre-#146 shape, reproduced from a real render of the previous image."""

    cdn = "https://cdn.jsdelivr.net/npm/swagger-ui-dist@latest/swagger-ui-bundle.js"
    base = stub_server(
        _healthy(
            **{
                "/api/docs/swagger/": (
                    200,
                    "text/html",
                    f'<html><head><link rel="stylesheet" href="{HASHED_SWAGGER_CSS}">'
                    f'<script src="{cdn}"></script></head></html>',
                    {},
                    False,
                )
            }
        )
    )

    result = run_script("assert-static-served.sh", base)

    assert result.returncode == 1
    assert "no CDN, no font host" in result.output
    assert cdn in result.output


def test_a_redoc_page_loading_google_fonts_fails(run_script, stub_server):
    """The regression the override template exists to prevent.

    ``REDOC_DIST = "SIDECAR"`` relocates the bundle and nothing else: the shipped
    ``drf_spectacular/redoc.html`` still links a stylesheet from fonts.googleapis.com. A
    check that only looked for jsDelivr would call this page self-hosted.
    """

    fonts = "https://fonts.googleapis.com/css2?family=Roboto"
    base = stub_server(
        _healthy(
            **{
                "/api/docs/redoc/": (
                    200,
                    "text/html",
                    f'<html><head><link rel="stylesheet" href="{fonts}"></head>'
                    f'<body><script src="{HASHED_REDOC_JS}"></script></body></html>',
                    {},
                    False,
                )
            }
        )
    )

    result = run_script("assert-static-served.sh", base)

    assert result.returncode == 1
    assert "fonts.googleapis.com" in result.output


def test_a_protocol_relative_asset_on_a_docs_page_fails(run_script, stub_server):
    """`//cdn.example/x.js` fetches from a third party exactly as an absolute URL does."""

    base = stub_server(
        _healthy(
            **{
                "/api/docs/swagger/": (
                    200,
                    "text/html",
                    f'<html><head><link rel="stylesheet" href="{HASHED_SWAGGER_CSS}">'
                    '<script src="//cdn.example.test/swagger-ui-bundle.js"></script></head></html>',
                    {},
                    False,
                )
            }
        )
    )

    result = run_script("assert-static-served.sh", base)

    assert result.returncode == 1
    assert "protocol-relative" in result.output


def test_an_inline_script_comment_is_not_read_as_a_protocol_relative_url(run_script, stub_server):
    """The healthy Swagger fixture carries a `//` line comment, as the real page does.

    Asserted explicitly rather than left to the happy-path test: folding the two greps
    into one `(https?:)?//` pattern makes every correctly self-hosted docs page fail, and
    the resulting error would point at the wrong thing entirely.
    """

    assert "//" in SWAGGER_HTML.split("<script>")[-1]

    assert run_script("assert-static-served.sh", stub_server(_healthy())).returncode == 0


def test_a_docs_page_referencing_no_sidecar_asset_fails(run_script, stub_server):
    """A page carrying only hashed assets from elsewhere proves nothing about the bundles."""

    base = stub_server(
        _healthy(
            **{
                "/api/docs/swagger/": (
                    200,
                    "text/html",
                    f'<html><head><link rel="stylesheet" href="{HASHED_CSS}"></head></html>',
                    {},
                    False,
                )
            }
        )
    )

    result = run_script("assert-static-served.sh", base)

    assert result.returncode == 1
    assert "no hashed drf_spectacular_sidecar asset" in result.output


def test_swagger_assets_do_not_satisfy_the_redoc_page_assertion(run_script, stub_server):
    """Each docs page is judged on its own references.

    Accumulating both pages' assets into one list before checking would let Swagger's
    bundle vouch for a Redoc page that had reverted to the CDN — and Redoc is the page
    with the extra failure mode (its own override template).
    """

    base = stub_server(
        _healthy(
            **{
                "/api/docs/redoc/": (
                    200,
                    "text/html",
                    f'<html><head><link rel="stylesheet" href="{HASHED_CSS}"></head></html>',
                    {},
                    False,
                )
            }
        )
    )

    result = run_script("assert-static-served.sh", base)

    assert result.returncode == 1
    assert "no hashed drf_spectacular_sidecar asset" in result.output


def test_a_docs_page_without_the_egress_csp_fails(run_script, stub_server):
    """A header can be stripped between the view and the browser.

    A proxy, a WSGI config, or a well-meaning "clean up the headers" change removes it and
    every other assertion in this gate still passes — while Redoc's bundle resumes calling
    cdn.redoc.ly on every page view. Nothing in the served HTML would show that.
    """

    base = stub_server(_healthy(**{"/api/docs/redoc/": (200, "text/html", REDOC_HTML, {}, False)}))

    result = run_script("assert-static-served.sh", base)

    assert result.returncode == 1
    assert "no Content-Security-Policy" in result.output


@pytest.mark.parametrize(
    "img_src",
    [
        # A wildcard permits the very request the directive is here to refuse.
        "img-src *",
        # And so does naming the host outright. This is the case a substring check for
        # "img-src 'self'" reported as enforced — the directive still starts with 'self'.
        "img-src 'self' https://cdn.redoc.ly",
        # A scheme source is just as broad in practice.
        "img-src 'self' https:",
    ],
)
def test_a_docs_page_whose_csp_allows_third_party_images_fails(run_script, stub_server, img_src):
    """img-src is the directive doing the work, and it is checked source by source.

    The reason it cannot be a substring test: every value above contains the exact string
    ``img-src 'self'`` except the wildcard, so a `grep` for that reported enforcement while
    Redoc's cdn.redoc.ly logo was still being fetched on every page view.
    """

    loose = {"Content-Security-Policy": f"default-src 'self'; {img_src}"}
    base = stub_server(
        _healthy(**{"/api/docs/redoc/": (200, "text/html", REDOC_HTML, loose, False)})
    )

    result = run_script("assert-static-served.sh", base)

    assert result.returncode == 1
    assert "Content-Security-Policy" in result.output


def test_a_docs_page_whose_csp_has_no_img_src_fails(run_script, stub_server):
    """No img-src means images fall back to default-src, which this gate cannot assume."""

    base = stub_server(
        _healthy(
            **{
                "/api/docs/redoc/": (
                    200,
                    "text/html",
                    REDOC_HTML,
                    {"Content-Security-Policy": "default-src 'self'"},
                    False,
                )
            }
        )
    )

    result = run_script("assert-static-served.sh", base)

    assert result.returncode == 1
    assert "no img-src directive" in result.output


def test_a_docs_page_whose_default_src_is_widened_fails(run_script, stub_server):
    # Same substring trap one directive over: "default-src 'self' *" contains
    # "default-src 'self'" and permits everything the policy was meant to refuse.
    wide = {"Content-Security-Policy": "default-src 'self' *; img-src 'self' data:"}
    base = stub_server(
        _healthy(**{"/api/docs/swagger/": (200, "text/html", SWAGGER_HTML, wide, False)})
    )

    result = run_script("assert-static-served.sh", base)

    assert result.returncode == 1
    assert "default-src *" in result.output


def test_the_shipped_policy_with_its_extra_directives_is_accepted(run_script, stub_server):
    """Directives beyond the two checked must not be treated as violations.

    The real policy also carries base-uri, form-action, script-src, style-src, font-src,
    connect-src, worker-src and child-src. A gate that rejected anything it did not
    recognise would fail every correct deployment.
    """

    real = {
        "Content-Security-Policy": (
            "default-src 'self'; base-uri 'self'; form-action 'self'; "
            "script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; font-src 'self' data:; connect-src 'self'; "
            "worker-src 'self' blob:; child-src blob:"
        )
    }
    base = stub_server(
        _healthy(
            **{
                "/api/docs/swagger/": (200, "text/html", SWAGGER_HTML, real, False),
                "/api/docs/redoc/": (200, "text/html", REDOC_HTML, real, False),
            }
        )
    )

    assert run_script("assert-static-served.sh", base).returncode == 0


def test_a_docs_asset_that_404s_fails(run_script, stub_server):
    """The page can render while the bundle it names is not actually served."""

    base = stub_server(_healthy(**{HASHED_SWAGGER_JS: (404, "text/html", "nope", {}, False)}))

    result = run_script("assert-static-served.sh", base)

    assert result.returncode == 1
    assert HASHED_SWAGGER_JS in result.output


# --- Transport and headers ---------------------------------------------------------------


def test_a_truncated_asset_transfer_fails(run_script, stub_server):
    """curl's -w output still reports '200 text/css' when the body dies part-way through,
    so the exit status has to be checked separately or a broken asset reads as healthy."""

    base = stub_server(_healthy(**{HASHED_CSS: (200, CSS, "body{}", {}, True)}))

    result = run_script("assert-static-served.sh", base)

    assert result.returncode == 1
    assert "did not complete" in result.output


def test_a_wrong_content_type_fails(run_script, stub_server):
    """A 200 carrying application/octet-stream still leaves the page unstyled."""

    base = stub_server(
        _healthy(**{HASHED_CSS: (200, "application/octet-stream", "body{}", {}, False)})
    )

    result = run_script("assert-static-served.sh", base)

    assert result.returncode == 1
    assert "will not apply" in result.output


@pytest.mark.parametrize("ctype", ["application/javascript", "text/javascript"])
def test_a_stylesheet_served_as_javascript_fails(run_script, stub_server, ctype):
    """The type has to match the EXTENSION, not just land in a combined allowlist.

    A browser refuses a stylesheet labelled as script exactly as firmly as one labelled
    octet-stream, so a shared allowlist reported success on an asset no page could use.
    Both hashed fixtures are .css here, so this also proves the check is applied per URL
    rather than once for the pair.
    """

    base = stub_server(
        _healthy(
            **{
                HASHED_CSS: (200, ctype, "body{}", {}, False),
                HASHED_DRF_CSS: (200, ctype, "body{}", {}, False),
            }
        )
    )

    result = run_script("assert-static-served.sh", base)

    assert result.returncode == 1
    assert "is a stylesheet but was served as" in result.output


@pytest.mark.parametrize("ctype", ["text/javascript", "application/javascript"])
def test_a_script_asset_is_accepted_under_either_javascript_spelling(
    run_script, stub_server, ctype
):
    """WhiteNoise emits text/javascript; older mimetypes tables say application/javascript.
    Neither may be rejected, or the gate fails on a correctly served .js asset."""

    base = stub_server(_healthy(**{HASHED_JS: (200, ctype, "1;", {}, False)}))

    assert run_script("assert-static-served.sh", base).returncode == 0


def test_a_broken_script_fails_even_when_every_stylesheet_is_healthy(run_script, stub_server):
    """The defect behind probing every asset rather than the first of each page.

    In the shipped templates stylesheets come first, so sampling one URL per page picked
    two .css files and never exercised a script at all — a deployment serving every .js as
    text/css, or 404ing it, passed with its scripted behaviour entirely broken.
    """

    base = stub_server(_healthy(**{HASHED_JS: (200, CSS, "1;", {}, False)}))

    result = run_script("assert-static-served.sh", base)

    assert result.returncode == 1
    assert "is a script but was served as" in result.output


def test_a_missing_script_fails_even_when_every_stylesheet_is_healthy(run_script, stub_server):
    base = stub_server(_healthy(**{HASHED_JS: (404, "text/html", "nope", {}, False)}))

    result = run_script("assert-static-served.sh", base)

    assert result.returncode == 1
    assert HASHED_JS in result.output


def test_a_broken_second_stylesheet_fails(run_script, stub_server):
    """Sampling the first asset also meant a later broken stylesheet went unnoticed."""

    base = stub_server(_healthy(**{HASHED_CSS_2: (404, "text/html", "nope", {}, False)}))

    result = run_script("assert-static-served.sh", base)

    assert result.returncode == 1
    assert HASHED_CSS_2 in result.output


@pytest.mark.parametrize(
    ("url_key", "ctype", "expected"),
    [
        # `text/css*` as a glob also matches `text/cssbogus`, which is a DIFFERENT subtype
        # rather than a parameterised form of the allowed one.
        ("css", "text/cssbogus", "is a stylesheet but was served as"),
        ("js", "application/javascriptbogus", "is a script but was served as"),
        ("js", "text/javascriptx", "is a script but was served as"),
    ],
)
def test_a_suffixed_subtype_is_not_mistaken_for_the_real_one(
    run_script, stub_server, url_key, ctype, expected
):
    target = HASHED_CSS if url_key == "css" else HASHED_JS
    base = stub_server(_healthy(**{target: (200, ctype, "x", {}, False)}))

    result = run_script("assert-static-served.sh", base)

    assert result.returncode == 1
    assert expected in result.output


@pytest.mark.parametrize("ctype", ["text / css", "text/\tcss", "te xt/css"])
def test_embedded_whitespace_in_a_media_type_is_rejected(run_script, stub_server, ctype):
    """Trimming must happen at the EDGES only.

    Deleting whitespace across the whole value folds `text / css` — which a browser's MIME
    parser rejects — into a passing `text/css`, so the comparison would no longer be the
    exact one it claims to be.
    """

    base = stub_server(_healthy(**{HASHED_CSS: (200, ctype, "body{}", {}, False)}))

    result = run_script("assert-static-served.sh", base)

    assert result.returncode == 1
    assert "is a stylesheet but was served as" in result.output


def test_a_broken_non_drf_asset_on_the_api_page_fails(run_script, stub_server):
    """The browsable-API extractor must collect every hashed asset on that page.

    Scoping it to /static/rest_framework/ meant an asset from anywhere else on the same
    page — a project-level override, say — was never probed: status, type and CORS all
    skipped while the page itself looked healthy.
    """

    custom = "/static/custom/theme.aabbccdd1122.css"
    api_html = (
        f'<html><head><link rel="stylesheet" href="{HASHED_DRF_CSS}">'
        f'<link rel="stylesheet" href="{custom}"></head></html>'
    )
    base = stub_server(
        _healthy(
            **{
                "/api/v2/tags": (403, "text/html", api_html, {}, False),
                custom: (404, "text/html", "nope", {}, False),
            }
        )
    )

    result = run_script("assert-static-served.sh", base)

    assert result.returncode == 1
    assert custom in result.output


def test_a_parameterised_media_type_is_still_accepted(run_script, stub_server):
    """The essence comparison must not reject the charset parameter WhiteNoise really
    sends, nor an upper-case spelling — media types are case-insensitive."""

    base = stub_server(
        _healthy(**{HASHED_CSS: (200, 'TEXT/CSS ; charset="utf-8"', "body{}", {}, False)})
    )

    assert run_script("assert-static-served.sh", base).returncode == 0


@pytest.mark.parametrize("origin", ["*", "https://admin.example.com"])
def test_any_cors_header_on_static_fails(run_script, stub_server, origin):
    """The contract is ABSENT, not merely 'not a wildcard'. WHITENOISE_ALLOW_ALL_ORIGINS is
    False in the shipped settings, so WhiteNoise emits no such header; its own default is
    True, and CI runs the shipped configuration."""

    base = stub_server(
        _healthy(
            **{HASHED_CSS: (200, CSS, "body{}", {"Access-Control-Allow-Origin": origin}, False)}
        )
    )

    result = run_script("assert-static-served.sh", base)

    assert result.returncode == 1
    assert "Access-Control-Allow-Origin" in result.output


def test_a_page_referencing_more_assets_than_the_ceiling_fails_before_probing(
    run_script, stub_server
):
    """The aggregate bound. Each probe is time-limited, but the COUNT was not, so a
    template regression emitting hundreds of assets turned a bounded per-request timeout
    into a job timeout. It must fail loudly and early rather than truncate the list, since
    silently probing a subset is the sampling defect this loop exists to avoid.
    """

    many = [f"/static/unfold/css/g{n:04d}.0123456789ab.css" for n in range(201)]
    links = "".join(f'<link rel="stylesheet" href="{u}">' for u in many)
    base = stub_server(
        _healthy(
            **{"/admin/login/": (200, "text/html", f"<html><head>{links}</head></html>", {}, False)}
        )
    )

    result = run_script("assert-static-served.sh", base)

    assert result.returncode == 1
    assert "over the 200 ceiling" in result.output
    # Bailed out before the loop, so nothing was probed.
    assert "ok    GET /static/" not in result.stdout


def test_the_probe_loop_stops_at_its_wall_clock_budget(run_script, stub_server):
    """The count ceiling does not bound the WORK — 200 assets at the per-request limit is
    far past the job timeout — so there is a total budget too. Driven here by setting the
    budget to zero, which is the mechanism rather than a real slow server."""

    base = stub_server(_healthy())

    result = run_script(
        "assert-static-served.sh",
        base,
        env={**os.environ, "OCTONOMY_PROBE_BUDGET_SECONDS": "0"},
    )

    assert result.returncode == 1
    assert "at or past the 0s budget" in result.output


def test_an_oversized_page_fails(run_script, stub_server):
    """`--max-filesize` bounds each body. Without it a pathological render could fill the
    runner's disk before extraction even began."""

    base = stub_server(
        _healthy(**{"/admin/login/": (200, "text/html", "x" * 6_000_000, {}, False)})
    )

    result = run_script("assert-static-served.sh", base)

    assert result.returncode == 1
    assert "did not complete" in result.output


def test_an_unreachable_host_fails_and_reports_the_real_curl_exit(run_script):
    """A container that never came up must fail loudly, not be reported as fine.

    The reported exit code is asserted, not just the failure: `$?` inside
    `if ! x=$(cmd); then` is the negation's own 0, so an earlier version printed
    "curl exit 0" for every transport error. Without this assertion that regression
    would slip back in unnoticed.
    """

    # Port 1 on loopback: reserved, and nothing in CI binds it. curl 7 = couldn't connect.
    result = run_script("assert-static-served.sh", "http://127.0.0.1:1")

    assert result.returncode == 1
    assert "curl exit 7" in result.output


def test_a_trailing_slash_on_the_base_url_is_accepted(run_script, stub_server):
    base = stub_server(_healthy())

    assert run_script("assert-static-served.sh", base + "/").returncode == 0


def test_usage_errors(run_script):
    assert run_script("assert-static-served.sh").returncode == 2
    assert run_script("assert-static-served.sh", "a", "b").returncode == 2
