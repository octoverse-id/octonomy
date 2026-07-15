"""Issue #43: the serialized outbox payload schema and its back-compat contract.

Namespace fields are *additive* JSON fields. Existing consumers ignore unknown
keys, so a global (NULL-namespace) event must still serialize to the historical
shape plus exactly ``namespace_type``/``namespace_id`` (both ``null``).
"""

from __future__ import annotations

import uuid

import pytest

from octonomy.events.dispatch import serialize_outbox_event
from octonomy.events.models import OutboxEvent

pytestmark = pytest.mark.django_db

# The exact set of keys the payload carried before issue #43. Consumers built
# against this shape must keep working; the only permitted delta is the two
# additive namespace keys asserted below.
HISTORICAL_OUTBOX_KEYS = {
    "id",
    "tenant_id",
    "application_id",
    "event_type",
    "aggregate_type",
    "aggregate_id",
    "operation_id",
    "request_id",
    "actor_id",
    "tag_id",
    "resource_type",
    "resource_id",
    "payload",
    "metadata",
}


def _make_event(**overrides) -> OutboxEvent:
    defaults = {
        "tenant_id": "tenant_a",
        "application_id": "commerce",
        "event_type": "tag.created",
        "aggregate_type": "tag",
        "aggregate_id": "agg-1",
        "payload": {"after": {"slug": "premium"}},
        "metadata": {},
        "operation_id": uuid.UUID("00000000-0000-0000-0000-000000000001"),
        "request_id": "req_1",
        "actor_id": "svc-x",
        "tag_id": uuid.UUID("00000000-0000-0000-0000-0000000000aa"),
        "resource_type": None,
        "resource_id": None,
    }
    defaults.update(overrides)
    return OutboxEvent.objects.create(**defaults)


def test_global_event_serializes_to_the_pinned_shape():
    event = _make_event()

    assert serialize_outbox_event(event) == {
        "id": str(event.id),
        "tenant_id": "tenant_a",
        "application_id": "commerce",
        "namespace_type": None,
        "namespace_id": None,
        "event_type": "tag.created",
        "aggregate_type": "tag",
        "aggregate_id": "agg-1",
        "operation_id": "00000000-0000-0000-0000-000000000001",
        "request_id": "req_1",
        "actor_id": "svc-x",
        "tag_id": "00000000-0000-0000-0000-0000000000aa",
        "resource_type": None,
        "resource_id": None,
        "payload": {"after": {"slug": "premium"}},
        "metadata": {},
    }


def test_namespace_fields_are_purely_additive():
    serialized = serialize_outbox_event(_make_event())

    # Every historical field is still present, and the only new keys are the two
    # namespace fields — nothing a legacy consumer relied on was renamed/removed.
    assert HISTORICAL_OUTBOX_KEYS <= set(serialized)
    assert set(serialized) - HISTORICAL_OUTBOX_KEYS == {"namespace_type", "namespace_id"}


def test_merchant_event_payload_carries_namespace():
    event = _make_event(namespace_type="merchant", namespace_id="merchant_a")

    serialized = serialize_outbox_event(event)

    assert serialized["namespace_type"] == "merchant"
    assert serialized["namespace_id"] == "merchant_a"
    # Namespace is the only thing that changed relative to the global shape.
    assert set(serialized) - HISTORICAL_OUTBOX_KEYS == {"namespace_type", "namespace_id"}
