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

import uuid

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
def test_bulk_partial_failure_is_not_a_cross_namespace_existence_oracle(merchant_a_client):
    # A foreign (merchant_b) tag id and a nonexistent id must be rejected
    # *identically*. If they differed (e.g. "Tag was not found" vs "Unknown tag
    # ids"), the response would reveal that the id names a real tag in another
    # namespace — an existence oracle across the isolation boundary.
    foreign = make_tag(
        application_id=APP,
        slug="secret-merchant-b-tag",
        name="Secret Merchant B Tag",
        **NS_B,
    )
    nonexistent = str(uuid.uuid4())

    def reject(tag_id: str):
        response = merchant_a_client.post(
            "/api/v2/tag-assignments/bulk-assign",
            {
                "application_id": APP,
                "tag_ids": [tag_id],
                "resource_type": "product",
                "resource_id": "bulk-oracle",
            },
            format="json",
        )
        assert response.status_code == 400, response.data
        return response.json()["error"]

    foreign_error = reject(str(foreign.id))
    missing_error = reject(nonexistent)

    # Same code and same field: the two are indistinguishable to the caller.
    assert foreign_error["code"] == missing_error["code"]
    assert set(foreign_error["details"]) == set(missing_error["details"])
    # And nothing about the foreign row's identity leaks either way.
    foreign_body = str(foreign_error)
    assert "merchant_b" not in foreign_body
    assert foreign.name not in foreign_body
    assert foreign.slug not in foreign_body


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


@override_settings(NAMESPACE_WRITE_ENABLED=True)
def test_event_streams_route_by_namespace_and_do_not_cross(merchant_a_client, merchant_b_client):
    # Both merchants write the same slug, so each emits an event. A consumer that
    # routes by namespace_id must receive exactly its own aggregate and never the
    # other merchant's — seeding both sides makes this a real routing assertion,
    # not the tautology of "no B row exists when only A wrote".
    ids = {}
    for client, merchant in ((merchant_a_client, "merchant_a"), (merchant_b_client, "merchant_b")):
        response = client.post(
            "/api/v2/tags",
            {"application_id": APP, "name": "Shared Slug", "slug": "sharedslug", "type": "label"},
            format="json",
        )
        assert response.status_code == 201, response.data
        ids[merchant] = response.json()["data"]["id"]

    for stream, own, other in (
        ("merchant_a", ids["merchant_a"], ids["merchant_b"]),
        ("merchant_b", ids["merchant_b"], ids["merchant_a"]),
    ):
        aggregates = set(
            OutboxEvent.objects.filter(namespace_id=stream, aggregate_type="tag").values_list(
                "aggregate_id", flat=True
            )
        )
        assert aggregates == {own}, (stream, aggregates)
        assert other not in aggregates
        # Audit reads route the same way.
        assert not AuditLog.objects.filter(namespace_id=stream, entity_id=other).exists()
