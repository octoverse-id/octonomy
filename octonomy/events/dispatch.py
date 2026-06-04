from __future__ import annotations

import logging

from django.db import connection, transaction
from django.utils import timezone

from octonomy.events.models import OutboxEvent

logger = logging.getLogger(__name__)


class LoggingEventTransport:
    def publish(self, event: OutboxEvent) -> None:
        logger.info(
            "outbox_event_published",
            extra={
                "event": {
                    "id": str(event.id),
                    "tenant_id": event.tenant_id,
                    "application_id": event.application_id,
                    "event_type": event.event_type,
                    "aggregate_type": event.aggregate_type,
                    "aggregate_id": event.aggregate_id,
                    "operation_id": str(event.operation_id) if event.operation_id else None,
                    "request_id": event.request_id,
                    "actor_id": event.actor_id,
                    "tag_id": str(event.tag_id) if event.tag_id else None,
                    "resource_type": event.resource_type,
                    "resource_id": event.resource_id,
                    "payload": event.payload,
                    "metadata": event.metadata,
                }
            },
        )


def dispatch_outbox_events(
    *,
    limit: int = 100,
    retry_failed: bool = False,
    transport: LoggingEventTransport | None = None,
) -> dict[str, int]:
    transport = transport or LoggingEventTransport()
    summary = {"published": 0, "failed": 0}

    for _ in range(limit):
        result = _dispatch_next_event(retry_failed=retry_failed, transport=transport)
        if result is None:
            break
        summary[result] += 1

    return summary


def _dispatch_next_event(
    *,
    retry_failed: bool,
    transport: LoggingEventTransport,
) -> str | None:
    with transaction.atomic():
        event = _next_event(retry_failed=retry_failed)
        if event is None:
            return None

        # The built-in transport only logs locally. Broker transports should move to an
        # explicit claim/publish/mark flow so network I/O does not hold this row lock.
        try:
            transport.publish(event)
        except Exception as exc:
            _mark_failed(event, exc)
            return "failed"

        _mark_published(event)
        return "published"


def _next_event(*, retry_failed: bool) -> OutboxEvent | None:
    statuses = [OutboxEvent.Status.PENDING]
    if retry_failed:
        statuses.append(OutboxEvent.Status.FAILED)

    queryset = OutboxEvent.objects.filter(
        status__in=statuses, available_at__lte=timezone.now()
    ).order_by("available_at", "created_at", "id")
    if getattr(connection.features, "has_select_for_update_skip_locked", False):
        queryset = queryset.select_for_update(skip_locked=True)
    return queryset.first()


def _mark_published(event: OutboxEvent) -> None:
    event.status = OutboxEvent.Status.PUBLISHED
    event.attempts += 1
    event.last_error = ""
    event.published_at = timezone.now()
    event.save(update_fields=["status", "attempts", "last_error", "published_at", "updated_at"])


def _mark_failed(event: OutboxEvent, exc: Exception) -> None:
    event.status = OutboxEvent.Status.FAILED
    event.attempts += 1
    event.last_error = str(exc)
    event.save(update_fields=["status", "attempts", "last_error", "updated_at"])
