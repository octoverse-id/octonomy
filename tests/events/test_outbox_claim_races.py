"""Claim-steal and recovery races in the outbox dispatcher (issue #46).

The dispatcher must not conflate an expired claim (a worker that vanished) with a
delivery failure:

- a stale claim holder can never mark an event failed once its claim was stolen
  (claim-token check on completion);
- recovery re-queues an expired claim for redelivery (PENDING), so a
  successfully-delivered-but-claim-expired event is never recorded as failed and
  never dead-lettered;
- recovery counts under ``recovered`` only and never bumps ``attempts``, so a row
  whose worker keeps dying is retried, not dead-lettered.
"""

from __future__ import annotations

import logging
import uuid

import pytest
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
        request_id="req_test",
    )


def force_expired_claim(event: OutboxEvent) -> None:
    event.status = OutboxEvent.Status.PROCESSING
    event.claim_id = uuid.uuid4()
    event.claimed_at = timezone.now() - timezone.timedelta(minutes=2)
    event.claim_expires_at = timezone.now() - timezone.timedelta(minutes=1)
    event.save(update_fields=["status", "claim_id", "claimed_at", "claim_expires_at", "updated_at"])


class RecordingTransport:
    def __init__(self):
        self.events = []

    def publish(self, event: OutboxEvent) -> None:
        self.events.append(event.id)


class ClaimStealingFailingTransport:
    """A concurrent worker re-claims the row, then this worker's publish fails."""

    def __init__(self, *, target_id, new_claim_id):
        self.target_id = target_id
        self.new_claim_id = new_claim_id

    def publish(self, event: OutboxEvent) -> None:
        if event.id == self.target_id:
            OutboxEvent.objects.filter(id=event.id).update(claim_id=self.new_claim_id)
        raise RuntimeError("boom after claim was stolen")


def test_stale_worker_failure_cannot_mark_a_stolen_event_failed(caplog):
    # A worker publishes (fails), but its claim was stolen mid-flight by another
    # worker (claim_id changed). The stale worker must NOT mark the event failed:
    # its completion is gated on still holding the claim token.
    event = make_event()
    other_worker_claim = uuid.uuid4()
    transport = ClaimStealingFailingTransport(target_id=event.id, new_claim_id=other_worker_claim)
    caplog.set_level(logging.WARNING, logger="octonomy.events.dispatch")

    summary = dispatch_outbox_events(limit=1, transport=transport, max_attempts=1)

    event.refresh_from_db()
    # No failure recorded by the stale worker: attempts untouched, not dead-lettered.
    assert summary == {"published": 0, "failed": 0, "dead_lettered": 0, "recovered": 0}
    assert event.attempts == 0
    assert event.status == OutboxEvent.Status.PROCESSING  # still held by the other worker
    assert event.claim_id == other_worker_claim
    assert any(record.message == "outbox_claim_lost" for record in caplog.records)


def test_recovered_event_is_redelivered_without_retry_failed():
    # Recovery re-queues to PENDING, so the event redelivers on a normal dispatch
    # cycle — it is not stranded behind the retry_failed sweep the way a FAILED
    # status would strand it.
    event = make_event()
    force_expired_claim(event)

    dispatch_outbox_events(limit=100, transport=RecordingTransport(), retry_base_seconds=1)
    event.refresh_from_db()
    assert event.status == OutboxEvent.Status.PENDING
    assert event.recoveries == 1
    assert event.attempts == 0

    # Make the backed-off row due, then dispatch normally (retry_failed defaults False).
    event.available_at = timezone.now() - timezone.timedelta(seconds=1)
    event.save(update_fields=["available_at", "updated_at"])
    transport = RecordingTransport()
    summary = dispatch_outbox_events(limit=100, transport=transport)

    event.refresh_from_db()
    assert transport.events == [event.id]
    assert summary["published"] == 1
    assert event.status == OutboxEvent.Status.PUBLISHED
    assert event.attempts == 1  # the redelivery is the first real delivery attempt
    assert event.recoveries == 1


def test_repeated_recoveries_never_dead_letter():
    # Even with max_attempts=1, a row whose claim keeps expiring is recovered
    # indefinitely: recoveries accrue, attempts stay 0, and it is never
    # dead-lettered — a claim expiry is not a delivery attempt.
    event = make_event()

    for expected_recoveries in range(1, 6):
        force_expired_claim(event)
        summary = dispatch_outbox_events(
            limit=100,
            transport=RecordingTransport(),
            max_attempts=1,
            retry_base_seconds=1,
        )
        event.refresh_from_db()
        assert summary == {"published": 0, "failed": 0, "dead_lettered": 0, "recovered": 1}
        assert event.status == OutboxEvent.Status.PENDING
        assert event.attempts == 0
        assert event.recoveries == expected_recoveries

    assert event.status != OutboxEvent.Status.DEAD_LETTER


def test_recovery_backoff_grows_with_recovery_count_and_caps():
    # Backoff is keyed to the recovery count, not a constant first-recovery delay:
    # each successive recovery of the same row waits longer, capped at retry_max.
    event = make_event()
    base, cap = 2, 20
    # min(cap, base * 2**(k-1)) for k = 1..6 -> 2, 4, 8, 16, 20, 20
    expected_delays = [2, 4, 8, 16, 20, 20]

    for recovery_number, expected in enumerate(expected_delays, start=1):
        force_expired_claim(event)
        before = timezone.now()
        dispatch_outbox_events(
            limit=100,
            transport=RecordingTransport(),
            retry_base_seconds=base,
            retry_max_seconds=cap,
        )
        event.refresh_from_db()
        assert event.recoveries == recovery_number
        delay = (event.available_at - before).total_seconds()
        # available_at = recovery_time + expected; recovery_time >= before, so the
        # measured delay is at least `expected` and only microseconds above it.
        assert expected <= delay < expected + 5, (recovery_number, delay, expected)


def test_recovery_preserves_accumulated_failure_backoff():
    # A row that already failed several delivery attempts carries a large retry
    # backoff. If its retry claim then expires, recovery must not reset the delay
    # to the small first-recovery backoff and hammer a still-failing destination:
    # the failure backoff for the accumulated attempts is a floor.
    event = make_event()
    event.attempts = 5  # accumulated real delivery failures
    event.save(update_fields=["attempts", "updated_at"])
    force_expired_claim(event)  # PROCESSING + expired; attempts unchanged

    base, cap = 2, 10_000
    before = timezone.now()
    dispatch_outbox_events(
        limit=100,
        transport=RecordingTransport(),
        retry_base_seconds=base,
        retry_max_seconds=cap,
    )

    event.refresh_from_db()
    assert event.recoveries == 1
    assert event.attempts == 5  # recovery never touches attempts
    # Floor = failure backoff for 5 attempts = base * 2**(5-1) = 32, far above the
    # first-recovery delay (base = 2). Recovery must respect that floor.
    failure_floor = base * 2 ** (5 - 1)
    delay = (event.available_at - before).total_seconds()
    assert delay >= failure_floor, (delay, failure_floor)
