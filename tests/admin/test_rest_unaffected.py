"""Regression: the admin middleware must not affect the REST API (#84).

Adding SessionMiddleware, CsrfViewMiddleware, AuthenticationMiddleware, and
MessageMiddleware for the admin must NOT change REST behavior. It cannot, because
DEFAULT_AUTHENTICATION_CLASSES is empty (DRF never runs SessionAuthentication or its
CSRF check) and DRF's APIView is csrf_exempt — but this is load-bearing, so lock it
with a test that POSTs with a bearer token and NO CSRF token / session cookie.
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
