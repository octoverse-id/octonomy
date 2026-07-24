"""NS-5 (#63): grant matching filters by tenant at the database.

tenant_grants moved from loading every grant the client holds and filtering in
Python to a tenant-scoped SQL query. These tests pin the two properties that
matters: it returns exactly the tenant's grants (equivalence), and it does not
load grants for other tenants (the fan-out reduction).
"""

from __future__ import annotations

import pytest
from django.test import override_settings
from django.urls import path
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework.test import APIClient

from octonomy.core.auth import (
    BearerTokenPermission,
    request_tenant_grants,
    tenant_grants,
)
from octonomy.service_auth.services import create_service_client_token

pytestmark = pytest.mark.django_db


@api_view(["GET"])
def _probe_view(request):
    return Response({"ok": True})


_probe_view.cls.permission_classes = [BearerTokenPermission]
_probe_view.cls.required_scopes = {"GET": "tags:read"}

urlpatterns = [path("probe", _probe_view)]


def _multi_tenant_client():
    # One service client granted two different tenants.
    _token, client = create_service_client_token(
        name="svc-multi-tenant",
        grants=[
            {"tenant_id": "tenant_a", "application_id": None, "scopes": ["tags:read"]},
            {"tenant_id": "tenant_b", "application_id": None, "scopes": ["tags:read"]},
        ],
    )
    return client


def test_tenant_grants_returns_only_the_requested_tenant():
    client = _multi_tenant_client()

    a = tenant_grants(client, "tenant_a")
    b = tenant_grants(client, "tenant_b")

    assert {g.tenant_id for g in a} == {"tenant_a"}
    assert {g.tenant_id for g in b} == {"tenant_b"}
    assert tenant_grants(client, "tenant_c") == []


def test_tenant_grants_is_a_single_query_scoped_to_the_tenant(django_assert_num_queries):
    client = _multi_tenant_client()

    # One query, and it fetches the tenant's grants only — not every grant the
    # client holds across all tenants.
    with django_assert_num_queries(1):
        grants = tenant_grants(client, "tenant_a")

    assert [g.tenant_id for g in grants] == ["tenant_a"]


def test_request_tenant_grants_caches_within_a_request(rf, django_assert_num_queries):
    client = _multi_tenant_client()
    request = rf.get("/probe")

    with django_assert_num_queries(1):
        first = request_tenant_grants(request, client, "tenant_a")
        second = request_tenant_grants(request, client, "tenant_a")

    assert first is second  # same cached list object, no second query


def test_request_tenant_grants_rekeys_on_a_different_tenant(rf):
    client = _multi_tenant_client()
    request = rf.get("/probe")

    a = request_tenant_grants(request, client, "tenant_a")
    b = request_tenant_grants(request, client, "tenant_b")

    assert {g.tenant_id for g in a} == {"tenant_a"}
    assert {g.tenant_id for g in b} == {"tenant_b"}


@override_settings(ROOT_URLCONF=__name__)
def test_authorized_request_does_not_load_other_tenant_grants(django_assert_max_num_queries):
    token, _client = create_service_client_token(
        name="svc-req",
        grants=[
            {"tenant_id": "tenant_a", "application_id": None, "scopes": ["tags:read"]},
            {"tenant_id": "tenant_b", "application_id": None, "scopes": ["tags:read"]},
        ],
    )
    api = APIClient()
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {token}", HTTP_X_TENANT_ID="tenant_a")

    # Client fetch + one tenant-scoped grant fetch (+ last-used update). The grant
    # query is bounded to tenant_a; tenant_b's grant is never materialised.
    with django_assert_max_num_queries(4):
        response = api.get("/probe")

    assert response.status_code == 200
