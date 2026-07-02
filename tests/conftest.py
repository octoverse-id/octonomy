from __future__ import annotations

import pytest
from rest_framework.test import APIClient

from octonomy.service_auth.services import create_service_client_token


@pytest.fixture
def service_token(db):
    token, _client = create_service_client_token(
        name="svc-tests",
        grants=[
            {
                "tenant_id": "tenant_a",
                "application_id": None,
                "scopes": ["tags:read", "tags:write", "audit:read"],
            }
        ],
    )
    return token


@pytest.fixture
def other_tenant_token(db):
    token, _client = create_service_client_token(
        name="svc-other-tests",
        grants=[
            {
                "tenant_id": "tenant_b",
                "application_id": None,
                "scopes": ["tags:read", "tags:write", "audit:read"],
            }
        ],
    )
    return token


@pytest.fixture
def api_client(service_token):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {service_token}", HTTP_X_TENANT_ID="tenant_a")
    return client


@pytest.fixture
def other_tenant_client(other_tenant_token):
    client = APIClient()
    client.credentials(
        HTTP_AUTHORIZATION=f"Bearer {other_tenant_token}", HTTP_X_TENANT_ID="tenant_b"
    )
    return client


@pytest.fixture
def merchant_token(db):
    """Exact merchant_a grant — fail-closed: authorized for merchant_a only, not global."""

    token, _client = create_service_client_token(
        name="svc-merchant-a",
        grants=[
            {
                "tenant_id": "tenant_a",
                "application_id": "commerce",
                "namespace_type": "merchant",
                "namespace_id": "merchant_a",
                "scopes": ["tags:read", "tags:write", "audit:read"],
            }
        ],
    )
    return token


@pytest.fixture
def wildcard_token(db):
    """Wildcard grant — authorized for global and any namespace inside commerce."""

    token, _client = create_service_client_token(
        name="svc-wildcard",
        grants=[
            {
                "tenant_id": "tenant_a",
                "application_id": "commerce",
                "namespace_wildcard": True,
                "scopes": ["tags:read", "tags:write", "audit:read"],
            }
        ],
    )
    return token
