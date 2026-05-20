from __future__ import annotations

import pytest

from octonomy.audit.models import AuditLog
from tests.factories import make_tag, make_vocabulary

pytestmark = pytest.mark.django_db


def test_tag_create_update_and_deactivate_write_audit_logs(api_client):
    create_response = api_client.post(
        "/api/v1/tags",
        {"name": "Featured", "slug": "featured", "type": "label", "metadata": {}},
        format="json",
        HTTP_X_ACTOR_ID="svc-tags",
    )
    tag_id = create_response.json()["data"]["id"]

    update_response = api_client.patch(
        f"/api/v1/tags/{tag_id}",
        {"name": "Featured Content"},
        format="json",
        HTTP_X_ACTOR_ID="svc-tags",
    )
    delete_response = api_client.delete(f"/api/v1/tags/{tag_id}", HTTP_X_ACTOR_ID="svc-tags")
    repeated_delete_response = api_client.delete(
        f"/api/v1/tags/{tag_id}",
        HTTP_X_ACTOR_ID="svc-tags",
    )

    assert create_response.status_code == 201
    assert update_response.status_code == 200
    assert delete_response.status_code == 204
    assert repeated_delete_response.status_code == 204

    logs = AuditLog.objects.order_by("created_at")
    assert list(logs.values_list("action", flat=True)) == [
        "tag.created",
        "tag.updated",
        "tag.deactivated",
    ]
    assert logs[0].actor_id == "svc-tags"
    assert logs[0].changes["after"]["slug"] == "featured"
    assert logs[1].changes == {
        "before": {"name": "Featured"},
        "after": {"name": "Featured Content"},
    }
    assert logs[2].changes == {
        "before": {"is_active": True},
        "after": {"is_active": False},
    }


def test_vocabulary_create_update_and_deactivate_write_audit_logs(api_client):
    create_response = api_client.post(
        "/api/v1/vocabularies",
        {"name": "Labels", "slug": "labels", "metadata": {}},
        format="json",
        HTTP_X_ACTOR_ID="svc-taxonomy",
    )
    vocabulary_id = create_response.json()["data"]["id"]

    update_response = api_client.patch(
        f"/api/v1/vocabularies/{vocabulary_id}",
        {"name": "Content Labels"},
        format="json",
        HTTP_X_ACTOR_ID="svc-taxonomy",
    )
    delete_response = api_client.delete(
        f"/api/v1/vocabularies/{vocabulary_id}",
        HTTP_X_ACTOR_ID="svc-taxonomy",
    )
    repeated_delete_response = api_client.delete(
        f"/api/v1/vocabularies/{vocabulary_id}",
        HTTP_X_ACTOR_ID="svc-taxonomy",
    )

    assert create_response.status_code == 201
    assert update_response.status_code == 200
    assert delete_response.status_code == 204
    assert repeated_delete_response.status_code == 204

    logs = AuditLog.objects.order_by("created_at")
    assert list(logs.values_list("action", flat=True)) == [
        "vocabulary.created",
        "vocabulary.updated",
        "vocabulary.deactivated",
    ]
    assert logs[0].actor_id == "svc-taxonomy"
    assert logs[0].entity_type == "vocabulary"
    assert logs[0].changes["after"]["slug"] == "labels"
    assert logs[1].changes == {
        "before": {"name": "Labels"},
        "after": {"name": "Content Labels"},
    }
    assert logs[2].changes == {
        "before": {"is_active": True},
        "after": {"is_active": False},
    }


def test_noop_vocabulary_update_does_not_write_audit_log(api_client):
    vocabulary = make_vocabulary(slug="labels", name="Labels")

    response = api_client.patch(
        f"/api/v1/vocabularies/{vocabulary.id}",
        {"name": "Labels"},
        format="json",
    )

    assert response.status_code == 200
    assert AuditLog.objects.count() == 0


def test_noop_tag_update_does_not_write_audit_log(api_client):
    tag = make_tag(slug="featured", name="Featured")

    response = api_client.patch(
        f"/api/v1/tags/{tag.id}",
        {"name": "Featured"},
        format="json",
    )

    assert response.status_code == 200
    assert AuditLog.objects.count() == 0


def test_assignment_create_and_delete_write_audit_only_for_actual_mutations(api_client):
    tag = make_tag(application_id="commerce", slug="sale")
    payload = {
        "application_id": "commerce",
        "tag_id": str(tag.id),
        "resource_type": "product",
        "resource_id": "prod_123",
        "assigned_by": "svc-catalog",
    }

    first_create = api_client.post(
        "/api/v1/tag-assignments",
        payload,
        format="json",
        HTTP_X_ACTOR_ID="svc-catalog",
    )
    repeated_create = api_client.post(
        "/api/v1/tag-assignments",
        payload,
        format="json",
        HTTP_X_ACTOR_ID="svc-catalog",
    )
    first_delete = api_client.delete(
        "/api/v1/tag-assignments",
        payload,
        format="json",
        HTTP_X_ACTOR_ID="svc-catalog",
    )
    repeated_delete = api_client.delete(
        "/api/v1/tag-assignments",
        payload,
        format="json",
        HTTP_X_ACTOR_ID="svc-catalog",
    )

    assert first_create.status_code == 201
    assert repeated_create.status_code == 200
    assert first_delete.status_code == 204
    assert repeated_delete.status_code == 204

    logs = AuditLog.objects.order_by("created_at")
    assert list(logs.values_list("action", flat=True)) == [
        "assignment.created",
        "assignment.removed",
    ]
    assert logs[0].actor_id == "svc-catalog"
    assert logs[0].tag_id == tag.id
    assert logs[0].changes["after"]["resource_id"] == "prod_123"
    assert logs[1].changes["before"]["resource_id"] == "prod_123"


def test_bulk_assign_and_remove_audit_only_changed_assignments(api_client):
    shared = make_tag(slug="featured")
    sale = make_tag(application_id="commerce", slug="sale")
    payload = {
        "application_id": "commerce",
        "resource_type": "product",
        "resource_id": "prod_123",
        "tag_ids": [str(shared.id), str(sale.id)],
        "assigned_by": "svc-catalog",
    }

    first_assign = api_client.post("/api/v1/tag-assignments/bulk-assign", payload, format="json")
    repeated_assign = api_client.post("/api/v1/tag-assignments/bulk-assign", payload, format="json")
    remove_one = api_client.post(
        "/api/v1/tag-assignments/bulk-remove",
        {**payload, "tag_ids": [str(shared.id)]},
        format="json",
    )
    repeated_remove = api_client.post(
        "/api/v1/tag-assignments/bulk-remove",
        {**payload, "tag_ids": [str(shared.id)]},
        format="json",
    )

    assert first_assign.json()["data"]["created"] == 2
    assert repeated_assign.json()["data"]["existing"] == 2
    assert remove_one.json()["data"]["removed"] == 1
    assert repeated_remove.json()["data"]["removed"] == 0

    assert AuditLog.objects.filter(action="assignment.created").count() == 2
    assert AuditLog.objects.filter(action="assignment.removed").count() == 1


def test_replace_resource_tags_groups_audit_rows_by_operation(api_client):
    featured = make_tag(slug="featured")
    archived = make_tag(slug="archived", type="state")
    sale = make_tag(application_id="commerce", slug="sale")

    api_client.post(
        "/api/v1/resources/product/prod_123/tags",
        {
            "application_id": "commerce",
            "tag_ids": [str(featured.id), str(archived.id)],
            "assigned_by": "svc-catalog",
        },
        format="json",
    )
    AuditLog.objects.all().delete()

    response = api_client.post(
        "/api/v1/resources/product/prod_123/tags",
        {
            "application_id": "commerce",
            "tag_ids": [str(featured.id), str(sale.id)],
            "assigned_by": "svc-catalog",
        },
        format="json",
    )

    assert response.status_code == 200
    logs = list(AuditLog.objects.order_by("action"))
    assert [log.action for log in logs] == ["assignment.created", "assignment.removed"]
    assert len({log.operation_id for log in logs}) == 1


def test_audit_log_endpoints_filter_and_enforce_tenant_scope(api_client, other_tenant_client):
    tag = make_tag(slug="featured")
    other_tag = make_tag(tenant_id="tenant_b", slug="hidden")
    api_client.post(
        "/api/v1/tag-assignments",
        {
            "application_id": "commerce",
            "tag_id": str(tag.id),
            "resource_type": "product",
            "resource_id": "prod_123",
            "assigned_by": "svc-catalog",
        },
        format="json",
        HTTP_X_ACTOR_ID="svc-catalog",
    )
    other_tenant_client.post(
        "/api/v1/tag-assignments",
        {
            "application_id": "commerce",
            "tag_id": str(other_tag.id),
            "resource_type": "product",
            "resource_id": "prod_999",
            "assigned_by": "svc-catalog",
        },
        format="json",
    )
    operation_id = AuditLog.objects.get(tenant_id="tenant_a").operation_id

    all_logs = api_client.get("/api/v1/audit-logs?action=assignment.created")
    tag_logs = api_client.get(f"/api/v1/tags/{tag.id}/audit-logs")
    resource_logs = api_client.get("/api/v1/resources/product/prod_123/audit-logs")
    actor_logs = api_client.get("/api/v1/audit-logs?actor_id=svc-catalog")
    operation_logs = api_client.get(f"/api/v1/audit-logs?operation_id={operation_id}")

    assert all_logs.status_code == 200
    assert len(all_logs.json()["data"]) == 1
    assert tag_logs.json()["data"][0]["tag_id"] == str(tag.id)
    assert resource_logs.json()["data"][0]["resource_id"] == "prod_123"
    assert actor_logs.json()["data"][0]["actor_id"] == "svc-catalog"
    assert operation_logs.json()["data"][0]["operation_id"] == str(operation_id)
