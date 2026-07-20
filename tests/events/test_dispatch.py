from __future__ import annotations

import hashlib
import hmac
import json
import logging
import uuid
from urllib import error as urllib_error

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone

from octonomy.events.dispatch import (
    LoggingEventTransport,
    WebhookEventTransport,
    dispatch_outbox_events,
    serialize_outbox_event,
    transport_from_settings,
)
from octonomy.events.models import OutboxEvent

pytestmark = pytest.mark.django_db


def make_event(event_type: str = "tag.created", **overrides) -> OutboxEvent:
    defaults = {
        "tenant_id": "tenant_a",
        "event_type": event_type,
        "aggregate_type": "tag",
        "aggregate_id": "tag-123",
        "payload": {"after": {"id": "tag-123"}},
        "metadata": {},
        "request_id": "req_test",
    }
    defaults.update(overrides)
    return OutboxEvent.objects.create(**defaults)


def make_namespaced_event(**overrides) -> OutboxEvent:
    return make_event(
        application_id="commerce",
        namespace_type="merchant",
        namespace_id="merchant_a",
        **overrides,
    )


class RecordingTransport:
    def __init__(self):
        self.events = []

    def publish(self, event: OutboxEvent) -> None:
        self.events.append(event.id)


class FailingTransport:
    def publish(self, event: OutboxEvent) -> None:
        raise RuntimeError(f"boom {event.id}")


class WebhookResponse:
    def __init__(self, status: int):
        self.status = status

    def getcode(self) -> int:
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False


class RecordingWebhookOpener:
    def __init__(self, *, response: WebhookResponse | None = None, exc: Exception | None = None):
        self.response = response or WebhookResponse(204)
        self.exc = exc
        self.requests = []

    def open(self, request, timeout):
        self.requests.append((request, timeout))
        if self.exc:
            raise self.exc
        return self.response


def test_dispatch_marks_pending_events_as_published():
    event = make_event()
    transport = RecordingTransport()

    summary = dispatch_outbox_events(limit=100, transport=transport)

    event.refresh_from_db()
    assert summary == {"published": 1, "failed": 0, "dead_lettered": 0, "recovered": 0}
    assert transport.events == [event.id]
    assert event.status == OutboxEvent.Status.PUBLISHED
    assert event.attempts == 1
    assert event.recoveries == 0
    assert event.published_at is not None
    assert event.last_error == ""
    assert event.claim_id is None
    assert event.claimed_at is None
    assert event.claim_expires_at is None


def test_dispatch_marks_failure_retryable_with_backoff():
    event = make_event()
    before_dispatch = timezone.now()

    summary = dispatch_outbox_events(
        limit=100,
        transport=FailingTransport(),
        max_attempts=3,
        retry_base_seconds=60,
    )

    event.refresh_from_db()
    assert summary == {"published": 0, "failed": 1, "dead_lettered": 0, "recovered": 0}
    assert event.status == OutboxEvent.Status.FAILED
    assert event.attempts == 1
    assert event.available_at >= before_dispatch + timezone.timedelta(seconds=60)
    assert "boom" in event.last_error
    assert event.claim_id is None


def test_dispatch_retries_due_failed_events_when_requested():
    event = make_event()
    dispatch_outbox_events(
        limit=100,
        transport=FailingTransport(),
        max_attempts=3,
        retry_base_seconds=0,
    )
    event.refresh_from_db()
    assert event.status == OutboxEvent.Status.FAILED
    assert event.available_at > timezone.now()
    event.available_at = timezone.now() - timezone.timedelta(seconds=1)
    event.save(update_fields=["available_at", "updated_at"])

    transport = RecordingTransport()
    summary = dispatch_outbox_events(
        limit=100,
        retry_failed=True,
        transport=transport,
    )

    event.refresh_from_db()
    assert summary == {"published": 1, "failed": 0, "dead_lettered": 0, "recovered": 0}
    assert transport.events == [event.id]
    assert event.status == OutboxEvent.Status.PUBLISHED
    assert event.attempts == 2


def test_dispatch_dead_letters_events_that_exceed_max_attempts():
    event = make_event()

    summary = dispatch_outbox_events(
        limit=100,
        transport=FailingTransport(),
        max_attempts=1,
    )

    event.refresh_from_db()
    assert summary == {"published": 0, "failed": 0, "dead_lettered": 1, "recovered": 0}
    assert event.status == OutboxEvent.Status.DEAD_LETTER
    assert event.attempts == 1
    assert "boom" in event.last_error


def test_dispatch_recovers_expired_claims():
    event = make_event()
    event.status = OutboxEvent.Status.PROCESSING
    event.claim_id = uuid.uuid4()
    event.claimed_at = timezone.now() - timezone.timedelta(minutes=2)
    event.claim_expires_at = timezone.now() - timezone.timedelta(minutes=1)
    event.save(
        update_fields=[
            "status",
            "claim_id",
            "claimed_at",
            "claim_expires_at",
            "updated_at",
        ]
    )

    summary = dispatch_outbox_events(
        limit=100,
        transport=RecordingTransport(),
        max_attempts=1,
        retry_base_seconds=0,
    )

    event.refresh_from_db()
    # A recovered claim is re-queued for redelivery (PENDING), not recorded as a
    # delivery failure: it counts only under "recovered", leaves attempts at 0, and
    # is never conflated into "failed".
    assert summary == {"published": 0, "failed": 0, "dead_lettered": 0, "recovered": 1}
    assert event.status == OutboxEvent.Status.PENDING
    assert event.attempts == 0
    assert event.recoveries == 1
    assert "claim expired" in event.last_error
    assert event.claim_id is None
    assert event.claimed_at is None
    assert event.claim_expires_at is None


def test_expired_claim_recovery_does_not_starve_pending_events():
    expired = make_event("tag.created")
    pending = make_event("tag.updated")
    expired.status = OutboxEvent.Status.PROCESSING
    expired.claim_id = uuid.uuid4()
    expired.claimed_at = timezone.now() - timezone.timedelta(minutes=2)
    expired.claim_expires_at = timezone.now() - timezone.timedelta(minutes=1)
    expired.save(
        update_fields=[
            "status",
            "claim_id",
            "claimed_at",
            "claim_expires_at",
            "updated_at",
        ]
    )
    transport = RecordingTransport()

    summary = dispatch_outbox_events(
        limit=1,
        transport=transport,
        max_attempts=3,
        retry_base_seconds=0,
    )

    expired.refresh_from_db()
    pending.refresh_from_db()
    assert summary == {"published": 1, "failed": 0, "dead_lettered": 0, "recovered": 1}
    assert transport.events == [pending.id]
    assert expired.status == OutboxEvent.Status.PENDING
    assert expired.attempts == 0
    assert expired.recoveries == 1
    assert pending.status == OutboxEvent.Status.PUBLISHED


def test_lost_claim_after_publish_logs_error_and_continues(caplog):
    first = make_event("tag.created")
    second = make_event("tag.updated")

    class StealingTransport:
        def __init__(self):
            self.events = []

        def publish(self, event: OutboxEvent) -> None:
            self.events.append(event.id)
            if event.id == first.id:
                # Simulate a concurrent recovery stealing the expired claim: it
                # re-queues the row as PENDING (redeliverable) with a backed-off
                # available_at, never FAILED.
                OutboxEvent.objects.filter(id=event.id).update(
                    status=OutboxEvent.Status.PENDING,
                    claim_id=None,
                    claimed_at=None,
                    claim_expires_at=None,
                    available_at=timezone.now() + timezone.timedelta(minutes=5),
                )

    transport = StealingTransport()
    caplog.set_level(logging.ERROR, logger="octonomy.events.dispatch")

    summary = dispatch_outbox_events(limit=2, transport=transport)

    first.refresh_from_db()
    second.refresh_from_db()
    assert summary == {"published": 1, "failed": 0, "dead_lettered": 0, "recovered": 0}
    assert transport.events == [first.id, second.id]
    # The stale worker cannot mark its lost claim: `first` is delivered but its
    # completion is a no-op (claim-token mismatch), so it is never recorded as
    # FAILED — it stays PENDING and will be redelivered (at-least-once).
    assert first.status == OutboxEvent.Status.PENDING
    assert second.status == OutboxEvent.Status.PUBLISHED
    assert any(record.message == "outbox_delivered_but_claim_lost" for record in caplog.records)


def test_dispatch_limit_is_respected():
    first = make_event("tag.created")
    second = make_event("tag.updated")
    transport = RecordingTransport()

    summary = dispatch_outbox_events(limit=1, transport=transport)

    first.refresh_from_db()
    second.refresh_from_db()
    assert summary == {"published": 1, "failed": 0, "dead_lettered": 0, "recovered": 0}
    assert len(transport.events) == 1
    assert first.status == OutboxEvent.Status.PUBLISHED
    assert second.status == OutboxEvent.Status.PENDING


def test_dispatch_skips_future_events():
    event = make_event()
    event.available_at = timezone.now() + timezone.timedelta(hours=1)
    event.save(update_fields=["available_at", "updated_at"])

    summary = dispatch_outbox_events(limit=100, transport=RecordingTransport())

    event.refresh_from_db()
    assert summary == {"published": 0, "failed": 0, "dead_lettered": 0, "recovered": 0}
    assert event.status == OutboxEvent.Status.PENDING


def test_dispatch_management_command_outputs_summary(capsys):
    make_event()

    call_command("dispatch_outbox_events", "--limit", "1")

    output = capsys.readouterr().out
    assert "published=1" in output
    assert "failed=0" in output
    assert "dead_lettered=0" in output
    assert "recovered=0" in output


def test_transport_from_settings_defaults_to_logging(settings):
    settings.OUTBOX_TRANSPORT = "logging"

    transport = transport_from_settings()

    assert isinstance(transport, LoggingEventTransport)


def test_transport_from_settings_requires_webhook_config(settings):
    settings.OUTBOX_TRANSPORT = "webhook"
    settings.OUTBOX_WEBHOOK_URL = ""
    settings.OUTBOX_WEBHOOK_SIGNING_SECRET = "secret"

    with pytest.raises(ImproperlyConfigured, match="OCTONOMY_WEBHOOK_URL"):
        transport_from_settings()


def test_transport_from_settings_requires_webhook_claim_timeout_margin(settings):
    settings.OUTBOX_TRANSPORT = "webhook"
    settings.OUTBOX_WEBHOOK_URL = "https://example.test/octonomy-events"
    settings.OUTBOX_WEBHOOK_SIGNING_SECRET = "secret"
    settings.OUTBOX_WEBHOOK_TIMEOUT_SECONDS = 10
    settings.OUTBOX_CLAIM_TIMEOUT_SECONDS = 10

    with pytest.raises(ImproperlyConfigured, match="CLAIM_TIMEOUT"):
        transport_from_settings()


def test_webhook_transport_rejects_non_http_urls():
    with pytest.raises(ImproperlyConfigured, match="http"):
        WebhookEventTransport(
            url="file:///tmp/octonomy-events",
            signing_secret="secret",
            timeout_seconds=3,
            opener=RecordingWebhookOpener(),
        )


def test_dispatch_requires_webhook_claim_timeout_margin():
    event = make_event()
    transport = WebhookEventTransport(
        url="https://example.test/octonomy-events",
        signing_secret="secret",
        timeout_seconds=3,
        opener=RecordingWebhookOpener(),
    )

    with pytest.raises(ImproperlyConfigured, match="claim_timeout_seconds"):
        dispatch_outbox_events(
            limit=100,
            transport=transport,
            claim_timeout_seconds=3,
        )

    event.refresh_from_db()
    assert event.status == OutboxEvent.Status.PENDING


def test_webhook_transport_posts_signed_event():
    event = make_event()
    opener = RecordingWebhookOpener(response=WebhookResponse(204))
    transport = WebhookEventTransport(
        url="https://example.test/octonomy-events",
        signing_secret="secret",
        timeout_seconds=3,
        opener=opener,
    )

    transport.publish(event)

    request, timeout = opener.requests[0]
    headers = dict(request.header_items())
    body = request.data
    assert timeout == 3
    assert request.full_url == "https://example.test/octonomy-events"
    assert request.get_method() == "POST"
    assert json.loads(body.decode("utf-8")) == serialize_outbox_event(event)
    assert headers["Content-type"] == "application/json"
    assert headers["X-octonomy-event-id"] == str(event.id)
    assert headers["X-octonomy-event-type"] == "tag.created"
    assert headers["X-octonomy-tenant-id"] == "tenant_a"
    assert headers["X-octonomy-request-id"] == "req_test"
    expected_signature = hmac.new(b"secret", body, hashlib.sha256).hexdigest()
    assert headers["X-octonomy-signature"] == f"sha256={expected_signature}"


def test_webhook_transport_raises_for_non_2xx_response():
    event = make_event()
    opener = RecordingWebhookOpener(response=WebhookResponse(503))
    transport = WebhookEventTransport(
        url="https://example.test/octonomy-events",
        signing_secret="secret",
        timeout_seconds=3,
        opener=opener,
    )

    with pytest.raises(RuntimeError, match="HTTP 503"):
        transport.publish(event)


def test_webhook_transport_raises_for_http_error():
    event = make_event()
    http_error = urllib_error.HTTPError(
        "https://example.test/octonomy-events",
        503,
        "Service Unavailable",
        hdrs=None,
        fp=None,
    )
    opener = RecordingWebhookOpener(exc=http_error)
    transport = WebhookEventTransport(
        url="https://example.test/octonomy-events",
        signing_secret="secret",
        timeout_seconds=3,
        opener=opener,
    )

    with pytest.raises(urllib_error.HTTPError):
        transport.publish(event)


def test_webhook_timeout_marks_event_failed():
    event = make_event()
    opener = RecordingWebhookOpener(exc=TimeoutError("timed out"))
    transport = WebhookEventTransport(
        url="https://example.test/octonomy-events",
        signing_secret="secret",
        timeout_seconds=3,
        opener=opener,
    )

    summary = dispatch_outbox_events(
        limit=100,
        transport=transport,
        max_attempts=3,
        retry_base_seconds=1,
    )

    event.refresh_from_db()
    assert summary == {"published": 0, "failed": 1, "dead_lettered": 0, "recovered": 0}
    assert event.status == OutboxEvent.Status.FAILED
    assert "timed out" in event.last_error


@override_settings(NAMESPACE_WRITE_ENABLED=False)
def test_dispatcher_pauses_namespaced_events_while_write_disabled():
    # The write kill-switch gates the dispatcher too: while off, global events are
    # delivered but namespaced events stay pending (never claimed or published).
    global_event = make_event()
    namespaced_event = make_namespaced_event()
    transport = RecordingTransport()

    dispatch_outbox_events(limit=10, transport=transport)

    assert global_event.id in transport.events
    assert namespaced_event.id not in transport.events
    global_event.refresh_from_db()
    namespaced_event.refresh_from_db()
    assert global_event.status == OutboxEvent.Status.PUBLISHED
    assert namespaced_event.status == OutboxEvent.Status.PENDING


@override_settings(NAMESPACE_WRITE_ENABLED=True)
def test_dispatcher_delivers_namespaced_events_when_write_enabled():
    namespaced_event = make_namespaced_event()
    transport = RecordingTransport()

    dispatch_outbox_events(limit=10, transport=transport)

    namespaced_event.refresh_from_db()
    assert namespaced_event.id in transport.events
    assert namespaced_event.status == OutboxEvent.Status.PUBLISHED


@override_settings(NAMESPACE_WRITE_ENABLED=False)
def test_dispatcher_skips_namespaced_expired_claim_recovery_while_write_disabled():
    # Expired-claim recovery is gated too: a namespaced processing row with an
    # expired claim is not recovered while writes are off (its row stays untouched).
    now = timezone.now()
    namespaced_event = make_namespaced_event(
        status=OutboxEvent.Status.PROCESSING,
        claim_id=uuid.uuid4(),
        claimed_at=now - timezone.timedelta(seconds=120),
        claim_expires_at=now - timezone.timedelta(seconds=60),
    )

    summary = dispatch_outbox_events(limit=10, transport=RecordingTransport())

    assert summary["recovered"] == 0
    namespaced_event.refresh_from_db()
    assert namespaced_event.status == OutboxEvent.Status.PROCESSING


@override_settings(NAMESPACE_WRITE_ENABLED=False)
def test_gated_expired_processing_row_stays_visible_in_lag_metric(caplog):
    # A namespaced event stuck in processing (claim expired, recovery gated off) must
    # still appear in lag_by_namespace_type — the gated backlog does not disappear.
    now = timezone.now()
    make_namespaced_event(
        status=OutboxEvent.Status.PROCESSING,
        claim_id=uuid.uuid4(),
        claimed_at=now - timezone.timedelta(seconds=120),
        claim_expires_at=now - timezone.timedelta(seconds=60),
    )

    caplog.set_level(logging.INFO, logger="octonomy.metrics")
    dispatch_outbox_events(limit=10, transport=RecordingTransport())

    summaries = [
        r for r in caplog.records if getattr(r, "metric", None) == "outbox_dispatch_summary"
    ]
    assert summaries
    lag = summaries[-1].metric_fields["lag_by_namespace_type"]
    assert lag.get("merchant", {}).get("backlog") == 1
