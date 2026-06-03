from __future__ import annotations

import pytest
from django.core.management import call_command
from django.utils import timezone

from octonomy.events.dispatch import dispatch_outbox_events
from octonomy.events.models import OutboxEvent

pytestmark = pytest.mark.django_db


def make_event(event_type: str = "tag.created") -> OutboxEvent:
    return OutboxEvent.objects.create(
        tenant_id="tenant_a",
        event_type=event_type,
        aggregate_type="tag",
        aggregate_id="tag-123",
        payload={"after": {"id": "tag-123"}},
        metadata={},
    )


class RecordingTransport:
    def __init__(self):
        self.events = []

    def publish(self, event: OutboxEvent) -> None:
        self.events.append(event.id)


class FailingTransport:
    def publish(self, event: OutboxEvent) -> None:
        raise RuntimeError(f"boom {event.id}")


def test_dispatch_marks_pending_events_as_published():
    event = make_event()
    transport = RecordingTransport()

    summary = dispatch_outbox_events(limit=100, transport=transport)

    event.refresh_from_db()
    assert summary == {"published": 1, "failed": 0}
    assert transport.events == [event.id]
    assert event.status == OutboxEvent.Status.PUBLISHED
    assert event.attempts == 1
    assert event.published_at is not None
    assert event.last_error == ""


def test_dispatch_marks_failures_and_retries_failed_events():
    event = make_event()

    first_summary = dispatch_outbox_events(limit=100, transport=FailingTransport())
    event.refresh_from_db()

    assert first_summary == {"published": 0, "failed": 1}
    assert event.status == OutboxEvent.Status.FAILED
    assert event.attempts == 1
    assert "boom" in event.last_error

    transport = RecordingTransport()
    retry_summary = dispatch_outbox_events(limit=100, retry_failed=True, transport=transport)
    event.refresh_from_db()

    assert retry_summary == {"published": 1, "failed": 0}
    assert transport.events == [event.id]
    assert event.status == OutboxEvent.Status.PUBLISHED
    assert event.attempts == 2


def test_dispatch_limit_is_respected():
    first = make_event("tag.created")
    second = make_event("tag.updated")
    transport = RecordingTransport()

    summary = dispatch_outbox_events(limit=1, transport=transport)

    first.refresh_from_db()
    second.refresh_from_db()
    assert summary == {"published": 1, "failed": 0}
    assert len(transport.events) == 1
    assert first.status == OutboxEvent.Status.PUBLISHED
    assert second.status == OutboxEvent.Status.PENDING


def test_dispatch_skips_future_events():
    event = make_event()
    event.available_at = timezone.now() + timezone.timedelta(hours=1)
    event.save(update_fields=["available_at", "updated_at"])

    summary = dispatch_outbox_events(limit=100, transport=RecordingTransport())

    event.refresh_from_db()
    assert summary == {"published": 0, "failed": 0}
    assert event.status == OutboxEvent.Status.PENDING


def test_dispatch_management_command_outputs_summary(capsys):
    make_event()

    call_command("dispatch_outbox_events", "--limit", "1")

    output = capsys.readouterr().out
    assert "published=1" in output
    assert "failed=0" in output
    assert "skipped" not in output
