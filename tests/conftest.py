from __future__ import annotations

import pytest
from rest_framework.test import APIClient


@pytest.fixture
def api_client():
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION="Bearer dev-token", HTTP_X_TENANT_ID="tenant_a")
    return client


@pytest.fixture
def other_tenant_client():
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION="Bearer dev-token", HTTP_X_TENANT_ID="tenant_b")
    return client
