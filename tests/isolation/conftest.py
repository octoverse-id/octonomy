from __future__ import annotations

import pytest
from rest_framework.test import APIClient

from octonomy.service_auth.services import create_service_client_token


def _merchant_client(token: str, namespace_type: str, namespace_id: str) -> APIClient:
    client = APIClient()
    client.credentials(
        HTTP_AUTHORIZATION=f"Bearer {token}",
        HTTP_X_TENANT_ID="tenant_a",
        HTTP_X_NAMESPACE_TYPE=namespace_type,
        HTTP_X_NAMESPACE_ID=namespace_id,
    )
    return client


@pytest.fixture
def merchant_b_token(db):
    """Exact merchant_b grant — the intruder in every isolation scenario."""

    token, _client = create_service_client_token(
        name="svc-merchant-b",
        grants=[
            {
                "tenant_id": "tenant_a",
                "application_id": "commerce",
                "namespace_type": "merchant",
                "namespace_id": "merchant_b",
                "scopes": ["tags:read", "tags:write", "audit:read"],
            }
        ],
    )
    return token


@pytest.fixture
def merchant_a_client(merchant_token) -> APIClient:
    return _merchant_client(merchant_token, "merchant", "merchant_a")


@pytest.fixture
def merchant_b_client(merchant_b_token) -> APIClient:
    return _merchant_client(merchant_b_token, "merchant", "merchant_b")
