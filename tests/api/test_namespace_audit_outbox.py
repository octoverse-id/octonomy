"""Issue #43: namespace propagation through audit logs and the outbox.

Two guarantees are exercised here:

* **Write side (hard gate):** a merchant mutation must stamp its namespace on
  both the audit row and the outbox event. Merchant mutations may never emit
  namespace-blind (global) audit/outbox rows; global mutations stay NULL/NULL.
* **Read side:** the audit list is namespace-filtered. A merchant-restricted
  grant reads only its own slice and global rows fail closed.
"""

from __future__ import annotations

import pytest
from django.test import override_settings
from rest_framework.test import APIClient

from octonomy.audit.models import AuditLog
from octonomy.events.models import OutboxEvent
from tests.factories import make_tag

pytestmark = pytest.mark.django_db

APP = "commerce"


def client_for(token, *, namespace_type=None, namespace_id=None):
    client = APIClient()
    creds = {"HTTP_AUTHORIZATION": f"Bearer {token}", "HTTP_X_TENANT_ID": "tenant_a"}
    if namespace_type is not None:
        creds["HTTP_X_NAMESPACE_TYPE"] = namespace_type
    if namespace_id is not None:
        creds["HTTP_X_NAMESPACE_ID"] = namespace_id
    client.credentials(**creds)
    return client


def _seed_audit_rows() -> None:
    """One audit row per scope, same tenant/app, so filtering is observable."""

    AuditLog.objects.create(
        tenant_id="tenant_a",
        application_id=APP,
        action="tag.created",
        entity_type="tag",
        entity_id="global-row",
    )
    AuditLog.objects.create(
        tenant_id="tenant_a",
        application_id=APP,
        namespace_type="merchant",
        namespace_id="merchant_a",
        action="tag.created",
        entity_type="tag",
        entity_id="merchant-a-row",
    )
    AuditLog.objects.create(
        tenant_id="tenant_a",
        application_id=APP,
        namespace_type="merchant",
        namespace_id="merchant_b",
        action="tag.created",
        entity_type="tag",
        entity_id="merchant-b-row",
    )


# --- write side: merchant mutations stamp namespace on audit + outbox ---------


@override_settings(NAMESPACE_WRITE_ENABLED=True)
def test_merchant_tag_mutation_stamps_namespace_on_audit_and_outbox(merchant_token):
    client = client_for(merchant_token, namespace_type="merchant", namespace_id="merchant_a")

    response = client.post(
        "/api/v2/tags",
        {"application_id": APP, "name": "Private", "slug": "private", "type": "label"},
        format="json",
        HTTP_X_ACTOR_ID="svc-merchant-a",
    )

    assert response.status_code == 201, response.data
    tag_id = response.json()["data"]["id"]

    audit = AuditLog.objects.get(action="tag.created")
    event = OutboxEvent.objects.get(event_type="tag.created")
    assert (audit.namespace_type, audit.namespace_id) == ("merchant", "merchant_a")
    assert (event.namespace_type, event.namespace_id) == ("merchant", "merchant_a")
    # The audit/outbox namespace matches the row they describe, not a blind global.
    assert audit.application_id == APP
    assert str(event.tag_id) == tag_id


@override_settings(NAMESPACE_WRITE_ENABLED=True)
def test_merchant_assignment_stamps_namespace_on_audit_and_outbox(merchant_token):
    tag = make_tag(
        application_id=APP,
        namespace_type="merchant",
        namespace_id="merchant_a",
        slug="sale",
    )
    client = client_for(merchant_token, namespace_type="merchant", namespace_id="merchant_a")

    response = client.post(
        f"/api/v2/tag-assignments?application_id={APP}",
        {
            "application_id": APP,
            "tag_id": str(tag.id),
            "resource_type": "product",
            "resource_id": "prod_1",
            "assigned_by": "svc-merchant-a",
        },
        format="json",
    )

    assert response.status_code == 201, response.data
    audit = AuditLog.objects.get(action="assignment.created")
    event = OutboxEvent.objects.get(event_type="assignment.created")
    assert (audit.namespace_type, audit.namespace_id) == ("merchant", "merchant_a")
    assert (event.namespace_type, event.namespace_id) == ("merchant", "merchant_a")


def test_global_mutation_leaves_audit_and_outbox_namespace_null(api_client):
    response = api_client.post(
        "/api/v2/tags",
        {"application_id": APP, "name": "Shared", "slug": "shared", "type": "label"},
        format="json",
    )

    assert response.status_code == 201, response.data
    audit = AuditLog.objects.get(action="tag.created")
    event = OutboxEvent.objects.get(event_type="tag.created")
    assert (audit.namespace_type, audit.namespace_id) == (None, None)
    assert (event.namespace_type, event.namespace_id) == (None, None)


# --- read side: audit list is namespace-filtered, fail-closed -----------------


def test_audit_list_merchant_grant_returns_only_its_namespace(merchant_token):
    _seed_audit_rows()
    client = client_for(merchant_token, namespace_type="merchant", namespace_id="merchant_a")

    response = client.get(f"/api/v2/audit-logs?application_id={APP}")

    assert response.status_code == 200, response.data
    rows = response.json()["data"]
    assert {row["entity_id"] for row in rows} == {"merchant-a-row"}
    assert all(row["namespace_id"] == "merchant_a" for row in rows)


def test_audit_list_merchant_include_global_stays_fail_closed(merchant_token):
    _seed_audit_rows()
    client = client_for(merchant_token, namespace_type="merchant", namespace_id="merchant_a")

    # An exact merchant grant is not authorized for global, so even an explicit
    # include_global opt-in must not surface the global row.
    response = client.get(f"/api/v2/audit-logs?application_id={APP}&include_global=true")

    assert response.status_code == 200, response.data
    rows = response.json()["data"]
    assert {row["entity_id"] for row in rows} == {"merchant-a-row"}


def test_audit_list_wildcard_include_global_merges_only_own_and_global(wildcard_token):
    _seed_audit_rows()
    client = client_for(wildcard_token, namespace_type="merchant", namespace_id="merchant_a")

    response = client.get(f"/api/v2/audit-logs?application_id={APP}&include_global=true")

    assert response.status_code == 200, response.data
    rows = response.json()["data"]
    # merchant_a + global merge in; another merchant's slice never leaks.
    assert {row["entity_id"] for row in rows} == {"merchant-a-row", "global-row"}
