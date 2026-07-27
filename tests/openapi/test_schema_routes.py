"""Per-version schema/docs HTTP routes (issue #42 follow-up).

Complements test_schema_versions.py, which exercises the generator directly. These
tests go through the URL + view stack to prove each route drives the generator at
its own version -- the regression guard for the bug where /api/schema/ was pinned to
DEFAULT_VERSION and every docs route served v1.
"""

from __future__ import annotations

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


def test_v2_schema_route_serves_the_v2_contract():
    response, schema = fetch_schema("/api/v2/schema/")
    assert response.status_code == 200
    assert "/api/v2/tags" in schema["paths"]
    assert not any(path.startswith("/api/v1/") for path in schema["paths"])
    assert has_namespace_header(schema)


def test_v1_schema_route_still_serves_the_v1_contract():
    response, schema = fetch_schema("/api/schema/")
    assert response.status_code == 200
    assert "/api/v1/tags" in schema["paths"]
    assert not any(path.startswith("/api/v2/") for path in schema["paths"])
    assert not has_namespace_header(schema)


def test_schema_routes_serve_distinct_versions():
    # The core of the fix: the two routes must not collapse to the same document.
    _, v1 = fetch_schema("/api/schema/")
    _, v2 = fetch_schema("/api/v2/schema/")
    assert has_namespace_header(v2) and not has_namespace_header(v1)
    assert set(v1["paths"]) != set(v2["paths"])


def test_swagger_ui_advertises_both_definitions():
    # The single Swagger page carries both schema URLs so the version dropdown
    # ("Select a definition") can switch between v1 and v2.
    response = APIClient().get("/api/docs/swagger/")
    body = response.content.decode()
    assert response.status_code == 200
    assert '"url": "/api/schema/"' in body
    assert '"url": "/api/v2/schema/"' in body


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
    assert '"url": "/octonomy/api/schema/"' in body
    assert '"url": "/octonomy/api/v2/schema/"' in body


def test_v2_redoc_route_is_served():
    response = APIClient().get("/api/docs/v2/redoc/")
    assert response.status_code == 200
