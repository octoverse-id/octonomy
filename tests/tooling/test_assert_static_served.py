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
