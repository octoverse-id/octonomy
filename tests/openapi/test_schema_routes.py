"""Per-version schema/docs HTTP routes (issue #42 follow-up).

Complements test_schema_versions.py, which exercises the generator directly. These
tests go through the URL + view stack to prove each route drives the generator at
its own version -- the regression guard for the bug where /api/schema/ was pinned to
DEFAULT_VERSION and every docs route served v1.
"""

from __future__ import annotations

import re

import yaml
from django.urls import set_script_prefix
from rest_framework.test import APIClient


def fetch_schema(path):
    # SpectacularAPIView negotiates to YAML by default; parse it back to a dict so
    # assertions read against the served bytes, not a re-generated schema.
    response = APIClient().get(path)
    return response, yaml.safe_load(response.content)


def has_namespace_header(schema):
    # X-Namespace-Type is injected only when the generator runs as v2, so its
    # presence in the *served* schema proves the route selected the v2 generator.
    for path_item in schema["paths"].values():
        for operation in path_item.values():
            if not isinstance(operation, dict):
                continue
            if any(p["name"] == "X-Namespace-Type" for p in operation.get("parameters", [])):
                return True
    return False


def redoc_schema_url(path):
    # A Redoc page embeds exactly one schema URL (its reversed url_name). Following it
    # proves which version the page actually renders -- a stronger guard than the
    # url_name string, since it also catches the schema route itself flipping version.
    response = APIClient().get(path)
    assert response.status_code == 200
    match = re.search(r"/[^\"'<> ]*schema/", response.content.decode())
    assert match, f"no schema URL embedded in {path}"
    return match.group(0)


def swagger_default_definition_url():
    # Resolve the definition the Swagger dropdown opens on: read urls.primaryName, then
    # map it back to its URL among the advertised definitions.
    body = APIClient().get("/api/docs/swagger/").content.decode()
    primary = re.search(r'"urls\.primaryName":\s*"([^"]+)"', body)
    assert primary, "swagger page exposes no urls.primaryName"
    urls = {
        name: url
        for url, name in re.findall(r'\{"url":\s*"([^"]+)",\s*"name":\s*"([^"]+)"\}', body)
    }
    name = primary.group(1)
    assert name in urls, f"primaryName {name!r} not among advertised definitions {urls}"
    return urls[name]


def test_v2_schema_route_serves_the_v2_contract():
    response, schema = fetch_schema("/api/v2/schema/")
    assert response.status_code == 200
    assert "/api/v2/tags" in schema["paths"]
    assert not any(path.startswith("/api/v1/") for path in schema["paths"])
    assert has_namespace_header(schema)


def test_default_schema_route_serves_the_v2_contract():
    # v2 is the primary/advertised surface: the un-versioned /api/schema/ route now
    # serves the v2 document (it served v1 before the v2-primary flip, issue #74).
    response, schema = fetch_schema("/api/schema/")
    assert response.status_code == 200
    assert "/api/v2/tags" in schema["paths"]
    assert not any(path.startswith("/api/v1/") for path in schema["paths"])
    assert has_namespace_header(schema)


def test_v1_schema_route_still_serves_the_v1_contract():
    # v1 stays fully browsable at its explicit route even though the default flipped.
    response, schema = fetch_schema("/api/v1/schema/")
    assert response.status_code == 200
    assert "/api/v1/tags" in schema["paths"]
    assert not any(path.startswith("/api/v2/") for path in schema["paths"])
    assert not has_namespace_header(schema)


def test_schema_routes_serve_distinct_versions():
    # The core of the fix: the two routes must not collapse to the same document.
    _, v1 = fetch_schema("/api/v1/schema/")
    _, v2 = fetch_schema("/api/v2/schema/")
    assert has_namespace_header(v2) and not has_namespace_header(v1)
    assert set(v1["paths"]) != set(v2["paths"])


def test_swagger_ui_advertises_both_definitions():
    # The single Swagger page carries both per-version schema URLs so the version
    # dropdown ("Select a definition") can switch between v1 and v2. The v1 tab points
    # at the explicit /api/v1/schema/ route -- not the default /api/schema/, which now
    # serves v2 -- and the page opens on the advertised v2 definition.
    response = APIClient().get("/api/docs/swagger/")
    body = response.content.decode()
    assert response.status_code == 200
    assert '"url": "/api/v1/schema/", "name": "v1"' in body
    assert '"url": "/api/v2/schema/", "name": "v2"' in body
    assert '"urls.primaryName": "v2"' in body


def test_swagger_dropdown_urls_carry_the_script_name_prefix():
    # Under a WSGI script-name prefix the dropdown must request the prefixed schema
    # URLs, matching NamespaceURLPathVersioning's path_info handling. Origin-absolute
    # URLs would drop the prefix and load the wrong (or no) schema. The real
    # WSGIHandler sets the prefix from SCRIPT_NAME; the test client does not, so set
    # it explicitly to reproduce that deployment context.
    set_script_prefix("/octonomy/")
    try:
        response = APIClient().get("/api/docs/swagger/")
        body = response.content.decode()
    finally:
        set_script_prefix("/")
    assert response.status_code == 200
    assert '"url": "/octonomy/api/v1/schema/", "name": "v1"' in body
    assert '"url": "/octonomy/api/v2/schema/", "name": "v2"' in body


def test_v2_redoc_resolves_to_the_v2_schema():
    # Follow the embedded schema URL (not just status 200) so wiring redoc-v2 to the
    # wrong schema would fail — symmetric with the default and v1 redoc guards below.
    _, schema = fetch_schema(redoc_schema_url("/api/docs/v2/redoc/"))
    assert has_namespace_header(schema)
    assert any(path.startswith("/api/v2/") for path in schema["paths"])


# --- default-docs version locks (issue #80) -------------------------------------
# The default redoc version was previously unguarded. These follow the schema URL
# each default docs page resolves to and assert the served contract's version, so
# they fail if the default docs silently revert to v1 or the v1 slots stop serving v1.


def test_default_swagger_definition_is_v2():
    # The dropdown opens on its primary definition; that definition must resolve to
    # the v2 contract (guards a silent revert of the advertised default to v1).
    _, schema = fetch_schema(swagger_default_definition_url())
    assert has_namespace_header(schema)
    assert any(path.startswith("/api/v2/") for path in schema["paths"])


def test_default_redoc_resolves_to_the_v2_schema():
    _, schema = fetch_schema(redoc_schema_url("/api/docs/redoc/"))
    assert has_namespace_header(schema)
    assert any(path.startswith("/api/v2/") for path in schema["paths"])


def test_v1_redoc_resolves_to_the_v1_schema():
    # /api/docs/v1/redoc/ keeps v1 browsable: it must embed and render the v1 schema.
    _, schema = fetch_schema(redoc_schema_url("/api/docs/v1/redoc/"))
    assert not has_namespace_header(schema)
    assert any(path.startswith("/api/v1/") for path in schema["paths"])
