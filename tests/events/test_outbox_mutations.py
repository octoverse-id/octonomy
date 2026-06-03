from __future__ import annotations

import pytest

from octonomy.audit.models import AuditLog
from octonomy.core.audit import AuditContext
from octonomy.core.errors import ConflictError
from octonomy.events.models import OutboxEvent
from octonomy.tags.services import create_tag
from tests.factories import make_alias, make_tag

pytestmark = pytest.mark.django_db


def test_tag_mutations_emit_events_for_actual_changes(api_client):
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
    noop_response = api_client.patch(
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
    assert noop_response.status_code == 200
    assert delete_response.status_code == 204
    assert repeated_delete_response.status_code == 204

    events = list(OutboxEvent.objects.order_by("created_at"))
    assert [event.event_type for event in events] == [
        "tag.created",
        "tag.updated",
        "tag.deactivated",
    ]
    assert events[0].aggregate_type == "tag"
    assert str(events[0].tag_id) == tag_id
    assert events[0].actor_id == "svc-tags"
    assert events[0].payload["after"]["slug"] == "featured"
    assert events[1].payload == {
        "before": {"name": "Featured"},
        "after": {"name": "Featured Content"},
    }
    assert events[2].payload == {
        "before": {"is_active": True},
        "after": {"is_active": False},
    }


def test_tag_deactivation_event_contains_cascaded_alias_ids(api_client):
    tag = make_tag(slug="featured")
    alias = make_alias(tag=tag, slug="promoted")

    response = api_client.delete(f"/api/v1/tags/{tag.id}")

    assert response.status_code == 204
    tag_event = OutboxEvent.objects.get(event_type="tag.deactivated")
    alias_event = OutboxEvent.objects.get(event_type="tag_alias.deactivated")
    assert tag_event.payload["cascaded_alias_ids"] == [str(alias.id)]
    assert alias_event.aggregate_id == str(alias.id)
    assert alias_event.tag_id == tag.id
    assert alias_event.payload["cascade"] == {
        "source_event_type": "tag.deactivated",
        "source_tag_id": str(tag.id),
    }
    assert alias_event.operation_id == tag_event.operation_id


def test_vocabulary_and_alias_mutations_emit_events(api_client):
    create_vocabulary_response = api_client.post(
        "/api/v1/vocabularies",
        {"name": "Labels", "slug": "labels", "metadata": {}},
        format="json",
    )
    vocabulary_id = create_vocabulary_response.json()["data"]["id"]
    api_client.patch(
        f"/api/v1/vocabularies/{vocabulary_id}",
        {"name": "Content Labels"},
        format="json",
    )
    api_client.delete(f"/api/v1/vocabularies/{vocabulary_id}")

    tag = make_tag(slug="featured")
    create_alias_response = api_client.post(
        "/api/v1/tag-aliases",
        {
            "tag_id": str(tag.id),
            "name": "Promoted",
            "slug": "promoted",
            "metadata": {},
        },
        format="json",
    )
    alias_id = create_alias_response.json()["data"]["id"]
    api_client.patch(
        f"/api/v1/tag-aliases/{alias_id}",
        {"name": "Hero"},
        format="json",
    )
    api_client.delete(f"/api/v1/tag-aliases/{alias_id}")

    assert list(OutboxEvent.objects.order_by("created_at").values_list("event_type", flat=True))[
        :6
    ] == [
        "vocabulary.created",
        "vocabulary.updated",
        "vocabulary.deactivated",
        "tag_alias.created",
        "tag_alias.updated",
        "tag_alias.deactivated",
    ]


def test_assignment_idempotency_controls_event_emission(api_client):
    tag = make_tag(application_id="commerce", slug="sale")
    payload = {
        "application_id": "commerce",
        "tag_id": str(tag.id),
        "resource_type": "product",
        "resource_id": "prod_123",
        "assigned_by": "svc-catalog",
    }

    first_create = api_client.post("/api/v1/tag-assignments", payload, format="json")
    repeated_create = api_client.post("/api/v1/tag-assignments", payload, format="json")
    first_delete = api_client.delete("/api/v1/tag-assignments", payload, format="json")
    repeated_delete = api_client.delete("/api/v1/tag-assignments", payload, format="json")

    assert first_create.status_code == 201
    assert repeated_create.status_code == 200
    assert first_delete.status_code == 204
    assert repeated_delete.status_code == 204
    event_types = list(
        OutboxEvent.objects.order_by("created_at").values_list("event_type", flat=True)
    )
    assert event_types == [
        "assignment.created",
        "assignment.removed",
    ]


def test_bulk_and_replace_assignment_events_use_actual_changes(api_client):
    featured = make_tag(slug="featured")
    archived = make_tag(slug="archived", type="state")
    sale = make_tag(application_id="commerce", slug="sale")

    assign_payload = {
        "application_id": "commerce",
        "resource_type": "product",
        "resource_id": "prod_123",
        "tag_ids": [str(featured.id), str(archived.id)],
        "assigned_by": "svc-catalog",
    }
    api_client.post("/api/v1/tag-assignments/bulk-assign", assign_payload, format="json")
    api_client.post("/api/v1/tag-assignments/bulk-assign", assign_payload, format="json")
    OutboxEvent.objects.all().delete()

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
    events = list(OutboxEvent.objects.order_by("event_type"))
    assert [event.event_type for event in events] == ["assignment.created", "assignment.removed"]
    assert len({event.operation_id for event in events}) == 1
    assert {event.resource_id for event in events} == {"prod_123"}


def test_failed_mutation_rolls_back_outbox_events():
    make_tag(slug="featured")
    context = AuditContext(actor_id="svc-tests", request_id="req_test", operation_id=None)

    with pytest.raises(ConflictError):
        create_tag(
            "tenant_a",
            {
                "application_id": None,
                "name": "Featured",
                "slug": "featured",
                "type": "label",
                "metadata": {},
                "is_active": True,
            },
            context,
        )

    assert OutboxEvent.objects.count() == 0
    assert AuditLog.objects.count() == 0


def test_service_mutation_without_audit_context_skips_outbox_event():
    create_tag(
        "tenant_a",
        {
            "application_id": None,
            "name": "Featured",
            "slug": "featured",
            "type": "label",
            "metadata": {},
            "is_active": True,
        },
    )

    assert OutboxEvent.objects.count() == 0


def test_outbox_events_carry_tenant_application_and_correlation(api_client):
    tag = make_tag(application_id="commerce", slug="sale")

    response = api_client.post(
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
        HTTP_X_REQUEST_ID="req_test",
    )

    assert response.status_code == 201
    event = OutboxEvent.objects.get(event_type="assignment.created")
    assert event.tenant_id == "tenant_a"
    assert event.application_id == "commerce"
    assert event.tag_id == tag.id
    assert event.resource_type == "product"
    assert event.resource_id == "prod_123"
    assert event.actor_id == "svc-catalog"
    assert event.request_id == "req_test"
    assert event.operation_id is not None
