from __future__ import annotations

import pytest

from octonomy.events.models import OutboxEvent
from octonomy.events.services import build_outbox_event

pytestmark = pytest.mark.django_db


def test_outbox_event_defaults():
    event = OutboxEvent.objects.create(
        tenant_id="tenant_a",
        event_type="tag.created",
        aggregate_type="tag",
        aggregate_id="tag-123",
        payload={"after": {"id": "tag-123"}},
    )

    assert event.status == OutboxEvent.Status.PENDING
    assert event.attempts == 0
    assert event.recoveries == 0
    assert event.metadata == {}
    assert event.available_at is not None
    assert event.claim_id is None
    assert event.claimed_at is None
    assert event.claim_expires_at is None


def test_outbox_event_payload_and_metadata_must_be_objects():
    with pytest.raises(ValueError, match="payload"):
        build_outbox_event(
            tenant_id="tenant_a",
            event_type="tag.created",
            aggregate_type="tag",
            aggregate_id="tag-123",
            payload=[],
        )

    with pytest.raises(ValueError, match="metadata"):
        build_outbox_event(
            tenant_id="tenant_a",
            event_type="tag.created",
            aggregate_type="tag",
            aggregate_id="tag-123",
            payload={},
            metadata=[],
        )
