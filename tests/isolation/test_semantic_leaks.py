"""Semantic leaks the registry sweep cannot catch (issue #44, criterion #2).

The sweep proves a merchant_b *read* never returns a merchant_a row. It cannot
reason about behaviours below the response body:

- **bulk operations with partial failures** — a bulk write mixing an authorized
  reference with a cross-namespace one must fail atomically, persist nothing, and
  not disclose that the foreign row exists;
- **event payload contents** — a namespaced write must stamp its audit/outbox
  rows with its own namespace so a consumer routing by namespace never receives
  another merchant's events.

The other semantic cases named in #44 already have homes and are not duplicated
here: detail 404 rule and count aggregation live in
``tests/api/test_v2_namespace_api.py``; alias resolution scope/order lives in
``tests/isolation/test_alias_resolution_order.py``; outbox payload back-compat
lives in ``tests/events/test_outbox_namespace_payload.py``.
"""

from __future__ import annotations

import pytest
from django.test import override_settings

from octonomy.assignments.models import TagAssignment
from octonomy.audit.models import AuditLog
from octonomy.events.dispatch import serialize_outbox_event
from octonomy.events.models import OutboxEvent
from tests.factories import make_tag
from tests.isolation.registry import APP, NS_A, NS_B

pytestmark = pytest.mark.django_db


# --- bulk operations with partial failures ------------------------------------


@override_settings(NAMESPACE_WRITE_ENABLED=True)
def test_bulk_assign_with_a_cross_namespace_reference_is_all_or_nothing(merchant_a_client):
    # A merchant_a bulk-assign lists one of its own tags and one belonging to
    # merchant_b. The foreign tag must sink the whole request atomically.
    own = make_tag(application_id=APP, slug="own-bulk", **NS_A)
    foreign = make_tag(application_id=APP, slug="foreign-bulk", **NS_B)

    response = merchant_a_client.post(
        "/api/v2/tag-assignments/bulk-assign",
        {
            "application_id": APP,
            "tag_ids": [str(own.id), str(foreign.id)],
            "resource_type": "product",
            "resource_id": "bulk-partial",
        },
        format="json",
    )

    assert response.status_code == 400, response.data
    # Not even the authorized half is written: the failure is atomic, so a caller
    # cannot smuggle a partial write past validation.
    assert not TagAssignment.objects.filter(resource_id="bulk-partial").exists()


@override_settings(NAMESPACE_WRITE_ENABLED=True)
def test_bulk_partial_failure_does_not_disclose_the_foreign_row(merchant_a_client):
    # The rejection must read as "not found", never "belongs to merchant_b" — a
    # partial failure must not become an existence oracle for another namespace.
    foreign = make_tag(
        application_id=APP,
        slug="secret-merchant-b-tag",
        name="Secret Merchant B Tag",
        namespace_type="merchant",
        namespace_id="merchant_b",
    )

    response = merchant_a_client.post(
        "/api/v2/tag-assignments/bulk-assign",
        {
            "application_id": APP,
            "tag_ids": [str(foreign.id)],
            "resource_type": "product",
            "resource_id": "bulk-oracle",
        },
        format="json",
    )

    assert response.status_code == 400
    body = str(response.data)
    assert "merchant_b" not in body
    assert foreign.name not in body
    assert foreign.slug not in body


# --- event payload contents (namespace partitioning) --------------------------


@override_settings(NAMESPACE_WRITE_ENABLED=True)
def test_namespaced_write_stamps_events_and_audit_with_its_own_namespace(merchant_a_client):
    response = merchant_a_client.post(
        "/api/v2/tags",
        {"application_id": APP, "name": "Evented", "slug": "evented", "type": "label"},
        format="json",
    )
    assert response.status_code == 201, response.data
    tag_id = response.json()["data"]["id"]

    outbox = OutboxEvent.objects.get(aggregate_type="tag", aggregate_id=tag_id)
    assert (outbox.namespace_type, outbox.namespace_id) == ("merchant", "merchant_a")
    serialized = serialize_outbox_event(outbox)
    assert serialized["namespace_type"] == "merchant"
    assert serialized["namespace_id"] == "merchant_a"

    audit = AuditLog.objects.get(entity_type="tag", entity_id=tag_id)
    assert (audit.namespace_type, audit.namespace_id) == ("merchant", "merchant_a")

    # A consumer routing merchant_b's stream must receive none of this write.
    assert not OutboxEvent.objects.filter(namespace_id="merchant_b").exists()
    assert not AuditLog.objects.filter(namespace_id="merchant_b").exists()


@override_settings(NAMESPACE_WRITE_ENABLED=True)
def test_two_merchants_writing_the_same_slug_emit_disjoint_event_streams(
    merchant_a_client, merchant_b_client
):
    for client in (merchant_a_client, merchant_b_client):
        response = client.post(
            "/api/v2/tags",
            {"application_id": APP, "name": "Shared Slug", "slug": "sharedslug", "type": "label"},
            format="json",
        )
        assert response.status_code == 201, response.data

    a_events = OutboxEvent.objects.filter(namespace_id="merchant_a", aggregate_type="tag")
    b_events = OutboxEvent.objects.filter(namespace_id="merchant_b", aggregate_type="tag")
    assert a_events.count() == 1
    assert b_events.count() == 1
    # The two writes share a slug but must not share an event: each namespace's
    # stream carries only its own aggregate id.
    assert set(a_events.values_list("aggregate_id", flat=True)).isdisjoint(
        b_events.values_list("aggregate_id", flat=True)
    )
