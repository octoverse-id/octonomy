"""Regression: admin-support middleware must not affect the REST API (#84, #143).

Adding SessionMiddleware, CsrfViewMiddleware, AuthenticationMiddleware, and
MessageMiddleware for the admin must NOT change REST behavior. It cannot, because
DEFAULT_AUTHENTICATION_CLASSES is empty (DRF never runs SessionAuthentication or its
CSRF check) and DRF's APIView is csrf_exempt — but this is load-bearing, so lock it
with a test that POSTs with a bearer token and NO CSRF token / session cookie.

#143 added WhiteNoiseMiddleware at index 1, ahead of all of the above and of the API
views. It only ever acts on paths under STATIC_URL, so an API request passes straight
through it untouched — asserted below, because "unconditional middleware in front of the
whole API" is exactly the kind of change that deserves a standing guard.
"""

from __future__ import annotations

import pytest
from rest_framework.test import APIClient

pytestmark = pytest.mark.django_db


def test_service_token_post_succeeds_without_csrf_token(service_token):
    # A bare APIClient: bearer token + tenant header only. No CSRF token, no session
    # cookie, no X-CSRFToken header. If CsrfViewMiddleware applied to the API, an
    # unsafe method (POST) would be rejected with 403 before the view ran.
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {service_token}", HTTP_X_TENANT_ID="tenant_a")

    response = client.post(
        "/api/v2/vocabularies",
        {
            "application_id": "commerce",
            "name": "Product Labels",
            "slug": "product-labels",
            "metadata": {},
        },
        format="json",
    )

    assert response.status_code == 201
    assert response.json()["data"]["slug"] == "product-labels"


def test_whitenoise_leaves_api_responses_untouched(api_client):
    # WhiteNoise short-circuits only for STATIC_URL paths. On an API path it must not
    # run at all: no static caching policy, no CORS header, and the request must still
    # reach RequestContextMiddleware (last in the chain) which stamps X-Request-ID and
    # the Vary headers that keep a shared cache from crossing tenants.
    response = api_client.get("/api/v2/tags")

    assert response.status_code == 200
    assert response["Content-Type"] == "application/json"
    assert "Access-Control-Allow-Origin" not in response.headers
    assert "Cache-Control" not in response.headers
    assert response["X-Request-ID"].startswith("req_")
    assert "X-Tenant-ID" in response["Vary"]
