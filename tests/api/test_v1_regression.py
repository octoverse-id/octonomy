"""v1 route regression (CRITICAL, issue #42).

Proves the v1/v2 shim left v1 unchanged: every v1 endpoint still resolves and
survives the injected ``version`` URL kwarg (no 500), and every v1 endpoint
rejects ``X-Namespace-*`` headers with the named 400 rather than silently
serving or writing the global namespace. Response bodies are covered by the
existing ~200 v1 API tests; this file guards routing and the header contract.
"""

from __future__ import annotations

import pytest

from tests.factories import make_alias, make_tag, make_vocabulary

pytestmark = pytest.mark.django_db


@pytest.fixture
def v1_fixtures(db):
    tag = make_tag(slug="featured", name="Featured")
    alias = make_alias(tag=tag, slug="promoted")
    vocabulary = make_vocabulary(slug="labels")
    return {"tag": tag, "alias": alias, "vocabulary": vocabulary}


def v1_get_endpoints(f):
    return [
        ("/api/v1/tags", {}),
        (f"/api/v1/tags/{f['tag'].id}", {}),
        (f"/api/v1/tags/{f['tag'].id}/aliases", {}),
        (f"/api/v1/tags/{f['tag'].id}/resources", {}),
        (f"/api/v1/tags/{f['tag'].id}/audit-logs", {}),
        ("/api/v1/vocabularies", {}),
        (f"/api/v1/vocabularies/{f['vocabulary'].id}", {}),
        ("/api/v1/tag-aliases", {}),
        (f"/api/v1/tag-aliases/{f['alias'].id}", {}),
        ("/api/v1/tag-resolution", {"slug": "featured"}),
        ("/api/v1/audit-logs", {}),
        ("/api/v1/resources/product/p1/tags", {"application_id": "commerce"}),
        ("/api/v1/resources/product/p1/audit-logs", {}),
    ]


def test_every_v1_get_endpoint_resolves_without_500(api_client, v1_fixtures):
    # A 200 on every route proves the captured version kwarg is absorbed by the
    # FBV wrapper — a signature mismatch would surface as a 500 here.
    for path, params in v1_get_endpoints(v1_fixtures):
        response = api_client.get(path, params)
        assert response.status_code == 200, (path, response.status_code, response.data)


def test_every_v1_get_endpoint_rejects_namespace_headers(api_client, v1_fixtures):
    for path, params in v1_get_endpoints(v1_fixtures):
        response = api_client.get(path, params, HTTP_X_NAMESPACE_TYPE="merchant")
        assert response.status_code == 400, (path, response.status_code)
        assert response.data["error"]["code"] == "namespace_not_supported", path


def test_v1_write_endpoints_reject_namespace_headers(api_client):
    for path in ("/api/v1/tags", "/api/v1/vocabularies", "/api/v1/tag-aliases"):
        response = api_client.post(
            path,
            {"name": "X", "slug": "x", "type": "label"},
            format="json",
            HTTP_X_NAMESPACE_ID="merchant_a",
        )
        assert response.status_code == 400, (path, response.status_code)
        assert response.data["error"]["code"] == "namespace_not_supported", path
