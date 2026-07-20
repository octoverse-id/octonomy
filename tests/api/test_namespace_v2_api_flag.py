"""V2 API edge gate — rollback step 1 (issue #45).

``NAMESPACE_V2_API_ENABLED=false`` withdraws the *namespaced* v2 surface: a
namespaced v2 request is refused before authentication with
``503 namespace_api_disabled``, while global v1/v2 traffic keeps working. This is
what lets "disable V2_API first" stop merchant traffic on rollback without
stranding global clients.
"""

from __future__ import annotations

import logging

import pytest
from django.test import override_settings

pytestmark = pytest.mark.django_db

MERCHANT_HEADERS = {"HTTP_X_NAMESPACE_TYPE": "merchant", "HTTP_X_NAMESPACE_ID": "merchant_a"}


@override_settings(NAMESPACE_V2_API_ENABLED=False)
def test_namespaced_v2_request_is_refused_when_api_disabled(api_client):
    response = api_client.get("/api/v2/tags?application_id=commerce", **MERCHANT_HEADERS)
    assert response.status_code == 503
    assert response.data["error"]["code"] == "namespace_api_disabled"


@override_settings(NAMESPACE_V2_API_ENABLED=False)
def test_disabled_v2_rejection_still_logs_version_and_namespace(api_client, caplog):
    # Rollback traffic must stay visible on the version/namespace dashboards: the
    # 503 is raised during version resolution, so the request log must still carry
    # version + namespace (mirrored before the edge gate) alongside the error_code.
    caplog.set_level(logging.INFO, logger="octonomy.requests")
    api_client.get("/api/v2/tags?application_id=commerce", **MERCHANT_HEADERS)
    record = next(r for r in caplog.records if r.message == "request_completed")

    assert record.status_code == 503
    assert record.version == "v2"
    assert record.namespace_type == "merchant"
    assert record.error_code == "namespace_api_disabled"


@override_settings(NAMESPACE_V2_API_ENABLED=False)
def test_global_v2_read_still_served_when_api_disabled(api_client):
    response = api_client.get("/api/v2/tags?application_id=commerce")
    assert response.status_code == 200


@override_settings(NAMESPACE_V2_API_ENABLED=False)
def test_global_v2_write_still_served_when_api_disabled(api_client):
    # Rollback keeps global writes flowing; only the namespaced surface is withdrawn.
    response = api_client.post(
        "/api/v2/tags",
        {"application_id": "commerce", "name": "Global", "slug": "global-tag", "type": "label"},
        format="json",
    )
    assert response.status_code == 201


@override_settings(NAMESPACE_V2_API_ENABLED=False)
def test_v1_request_still_served_when_api_disabled(api_client):
    response = api_client.get("/api/v1/tags?application_id=commerce")
    assert response.status_code == 200


def test_namespaced_v2_request_passes_the_gate_when_api_enabled(api_client):
    # With the surface enabled the request reaches auth (403 here — the tenant-wide
    # token is not authorized for the merchant namespace), never the 503 gate.
    response = api_client.get("/api/v2/tags?application_id=commerce", **MERCHANT_HEADERS)
    assert response.status_code != 503
    assert response.status_code == 403
