"""Read-only diagnostics admin (#85): AuditLog / OutboxEvent / ServiceClient / grants.

These registrations are view/list/search/filter only, and — critically — a service
token's ``hashed_key`` must never appear anywhere the admin renders.
"""

from __future__ import annotations

import pytest

from octonomy.audit.models import AuditLog
from octonomy.events.models import OutboxEvent
from octonomy.service_auth.models import ServiceClient, ServiceClientGrant
from tests.admin.conftest import admin_enabled

pytestmark = pytest.mark.django_db

SECRET_HASH = "THIS_IS_A_SECRET_HASHED_KEY_VALUE_0123456789"

DIAGNOSTIC_CHANGELISTS = [
    "/admin/audit/auditlog/",
    "/admin/events/outboxevent/",
    "/admin/service_auth/serviceclient/",
    "/admin/service_auth/serviceclientgrant/",
]
DIAGNOSTIC_ADD_URLS = [
    "/admin/audit/auditlog/add/",
    "/admin/events/outboxevent/add/",
    "/admin/service_auth/serviceclient/add/",
    "/admin/service_auth/serviceclientgrant/add/",
]


@pytest.fixture
def service_client_row(db):
    return ServiceClient.objects.create(
        name="diag-svc",
        key_prefix="octk_diag",
        hashed_key=SECRET_HASH,
        metadata={"team": "platform"},
    )


@pytest.mark.parametrize("url", DIAGNOSTIC_CHANGELISTS)
def test_diagnostic_changelists_render(url, client, superuser):
    client.force_login(superuser)
    with admin_enabled(True):
        response = client.get(url)
    assert response.status_code == 200


@pytest.mark.parametrize("url", DIAGNOSTIC_ADD_URLS)
def test_diagnostic_add_views_are_denied(url, client, superuser):
    # No add permission => the mutation endpoint is refused (POST included).
    client.force_login(superuser)
    with admin_enabled(True):
        get_response = client.get(url)
        post_response = client.post(url, {})
    assert get_response.status_code == 403
    assert post_response.status_code == 403


def test_diagnostic_admins_deny_mutation_permissions(client, superuser):
    from django.contrib import admin as django_admin

    class _Req:
        def __init__(self, user):
            self.user = user
            self.GET = {}

    request = _Req(superuser)
    for model in (AuditLog, OutboxEvent, ServiceClient, ServiceClientGrant):
        model_admin = django_admin.site._registry[model]
        assert model_admin.has_add_permission(request) is False
        assert model_admin.has_change_permission(request) is False
        assert model_admin.has_delete_permission(request) is False
        assert model_admin.get_actions(request) == {}


def test_service_client_hashed_key_never_rendered(client, superuser, service_client_row):
    client.force_login(superuser)
    with admin_enabled(True):
        changelist = client.get("/admin/service_auth/serviceclient/")
        detail = client.get(f"/admin/service_auth/serviceclient/{service_client_row.pk}/change/")

    assert changelist.status_code == 200
    assert detail.status_code == 200
    # Safe fields render; the secret hash never does.
    assert b"octk_diag" in changelist.content
    assert SECRET_HASH.encode() not in changelist.content
    assert SECRET_HASH.encode() not in detail.content
    assert b"hashed_key" not in detail.content


def test_audit_log_detail_renders_json_readably(client, superuser):
    audit = AuditLog.objects.create(
        tenant_id="tenant_a",
        action="tag.created",
        entity_type="tag",
        entity_id="abc",
        namespace_type="merchant",
        namespace_id="merchant_a",
        application_id="commerce",
        changes={"after": {"slug": "featured"}},
        metadata={"origin": "admin"},
    )
    client.force_login(superuser)
    with admin_enabled(True):
        response = client.get(f"/admin/audit/auditlog/{audit.pk}/change/")
    assert response.status_code == 200
    assert b"featured" in response.content  # the JSON changes render on the page


def test_outbox_event_detail_renders_json_readably(client, superuser):
    event = OutboxEvent.objects.create(
        tenant_id="tenant_a",
        event_type="tag.created",
        aggregate_type="tag",
        aggregate_id="abc",
        payload={"after": {"slug": "promo"}},
    )
    client.force_login(superuser)
    with admin_enabled(True):
        response = client.get(f"/admin/events/outboxevent/{event.pk}/change/")
    assert response.status_code == 200
    assert b"promo" in response.content


def test_service_client_grant_detail_renders_scopes(client, superuser, service_client_row):
    grant = ServiceClientGrant.objects.create(
        service_client=service_client_row,
        tenant_id="tenant_a",
        application_id="commerce",
        scopes=["tags:read", "tags:write"],
    )
    client.force_login(superuser)
    with admin_enabled(True):
        response = client.get(f"/admin/service_auth/serviceclientgrant/{grant.pk}/change/")
    assert response.status_code == 200
    assert b"tags:read" in response.content


def test_service_client_admin_field_allowlist_excludes_hashed_key(client, superuser):
    from django.contrib import admin as django_admin

    model_admin = django_admin.site._registry[ServiceClient]
    assert "hashed_key" not in model_admin.fields
    assert "hashed_key" not in model_admin.list_display
    assert "hashed_key" not in model_admin.get_readonly_fields(object())
