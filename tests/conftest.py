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
