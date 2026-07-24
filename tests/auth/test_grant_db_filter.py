"""NS-5 (#63): grant matching filters by tenant at the database.

tenant_grants moved from loading every grant the client holds and filtering in
Python to a tenant-scoped SQL query. These tests pin the properties that matter:
the filter runs in SQL (not Python after loading everything), it returns exactly
the tenant's grants regardless of shape, and the per-request cache is scoped to
the (client, tenant) principal.
"""

from __future__ import annotations

import pytest
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import path
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework.test import APIClient

from octonomy.core.auth import (
    GLOBAL_SCOPE,
    BearerTokenPermission,
    authorized_application_ids,
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


def _multi_shape_client():
    """One client with several tenant_a grant shapes plus a tenant_b lookalike."""

    _token, client = create_service_client_token(
        name="svc-multi-shape",
        grants=[
            {"tenant_id": "tenant_a", "application_id": None, "scopes": ["tags:read"]},
            {"tenant_id": "tenant_a", "application_id": "commerce", "scopes": ["tags:read"]},
            {
                "tenant_id": "tenant_a",
                "application_id": "commerce",
                "namespace_type": "merchant",
                "namespace_id": "m1",
                "scopes": ["tags:read"],
            },
            {"tenant_id": "tenant_b", "application_id": "commerce", "scopes": ["tags:read"]},
        ],
    )
    return client


def _grant_queries(captured) -> list[dict]:
    return [q for q in captured if "grant" in q["sql"].lower()]


def _assert_tenant_predicate(sql: str, tenant_id: str) -> None:
    # Prove the tenant filter is in the WHERE clause, not merely selected as a
    # column: the old Python-filter query also SELECTs tenant_id, so a bare
    # "tenant_id in sql" would pass without any tenant predicate.
    lowered = sql.lower()
    parts = lowered.split("where", 1)
    assert len(parts) == 2, f"no WHERE clause: {sql}"
    assert "tenant_id" in parts[1], f"tenant_id not filtered in WHERE: {sql}"
    assert tenant_id in sql, f"tenant value not bound in query: {sql}"


def test_tenant_grants_returns_every_shape_for_the_tenant_and_nothing_else():
    client = _multi_shape_client()

    a = tenant_grants(client, "tenant_a")
    b = tenant_grants(client, "tenant_b")

    assert len(a) == 3 and {g.tenant_id for g in a} == {"tenant_a"}
    assert len(b) == 1 and {g.tenant_id for g in b} == {"tenant_b"}
    assert tenant_grants(client, "tenant_c") == []


def test_tenant_grants_filters_in_sql_not_python():
    # The distinguishing property vs the old implementation: the tenant filter is
    # in the WHERE clause (one bounded query), not a Python filter after loading
    # every grant the client holds.
    client = _multi_shape_client()

    with CaptureQueriesContext(connection) as ctx:
        grants = tenant_grants(client, "tenant_a")

    grant_queries = _grant_queries(ctx.captured_queries)
    assert len(grant_queries) == 1
    _assert_tenant_predicate(grant_queries[0]["sql"], "tenant_a")
    assert len(grants) == 3


def test_request_tenant_grants_caches_within_a_request(rf):
    client = _multi_shape_client()
    request = rf.get("/probe")

    with CaptureQueriesContext(connection) as ctx:
        first = request_tenant_grants(request, client, "tenant_a")
        second = request_tenant_grants(request, client, "tenant_a")

    assert first is second  # same cached list, no second query
    assert len(_grant_queries(ctx.captured_queries)) == 1


def test_request_tenant_grants_rekeys_on_a_different_tenant(rf):
    client = _multi_shape_client()
    request = rf.get("/probe")

    a = request_tenant_grants(request, client, "tenant_a")
    b = request_tenant_grants(request, client, "tenant_b")

    assert {g.tenant_id for g in a} == {"tenant_a"}
    assert {g.tenant_id for g in b} == {"tenant_b"}


def test_request_tenant_grants_rekeys_on_a_different_client(rf):
    # The cache is keyed on (client, tenant): a request object reused across clients
    # must never return the first client's grants for the same tenant.
    _t1, client_one = create_service_client_token(
        name="svc-one",
        grants=[{"tenant_id": "tenant_a", "application_id": "one", "scopes": ["tags:read"]}],
    )
    _t2, client_two = create_service_client_token(
        name="svc-two",
        grants=[{"tenant_id": "tenant_a", "application_id": "two", "scopes": ["tags:read"]}],
    )
    request = rf.get("/probe")

    one = request_tenant_grants(request, client_one, "tenant_a")
    two = request_tenant_grants(request, client_two, "tenant_a")

    assert {g.application_id for g in one} == {"one"}
    assert {g.application_id for g in two} == {"two"}


def test_cache_shared_across_has_permission_and_object_lookup(rf):
    # has_permission's fetch and the later authorized_application_ids (object-by-id)
    # lookup share one grant query via the request cache.
    client = _multi_shape_client()
    request = rf.get("/probe")
    request.service_client = client
    request.tenant_id = "tenant_a"
    request.scope_context = GLOBAL_SCOPE
    request.required_scope = "tags:read"

    with CaptureQueriesContext(connection) as ctx:
        request_tenant_grants(request, client, "tenant_a")  # has_permission's fetch
        authorized_application_ids(request)  # object-by-id lookup reuses the cache

    assert len(_grant_queries(ctx.captured_queries)) == 1


@override_settings(ROOT_URLCONF=__name__)
def test_authenticated_request_issues_one_tenant_scoped_grant_query():
    client_token, _client = create_service_client_token(
        name="svc-req",
        grants=[
            {"tenant_id": "tenant_a", "application_id": None, "scopes": ["tags:read"]},
            {"tenant_id": "tenant_b", "application_id": None, "scopes": ["tags:read"]},
        ],
    )
    api = APIClient()
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {client_token}", HTTP_X_TENANT_ID="tenant_a")

    with CaptureQueriesContext(connection) as ctx:
        response = api.get("/probe")

    assert response.status_code == 200
    grant_queries = _grant_queries(ctx.captured_queries)
    assert len(grant_queries) == 1
    _assert_tenant_predicate(grant_queries[0]["sql"], "tenant_a")
