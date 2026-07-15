from __future__ import annotations

import logging
from typing import Any

from octonomy.core.audit import AuditContext
from octonomy.events.models import OutboxEvent

logger = logging.getLogger(__name__)


def validate_json_object(field: str, value: Any) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be a JSON object.")
    return value


def build_outbox_event(
    *,
    tenant_id: str,
    event_type: str,
    aggregate_type: str,
    aggregate_id: str,
    payload: dict,
    application_id: str | None = None,
    namespace_type: str | None = None,
    namespace_id: str | None = None,
    metadata: dict | None = None,
    audit_context: AuditContext | None = None,
    tag_id=None,
    resource_type: str | None = None,
    resource_id: str | None = None,
) -> OutboxEvent | None:
    payload = validate_json_object("payload", payload)
    metadata = validate_json_object("metadata", {} if metadata is None else metadata)
    if audit_context is None:
        logger.warning(
            "Outbox event skipped: no audit_context for event_type=%s aggregate_type=%s "
            "aggregate_id=%s",
            event_type,
            aggregate_type,
            aggregate_id,
        )
        return None

    return OutboxEvent(
        tenant_id=tenant_id,
        application_id=application_id,
        namespace_type=namespace_type,
        namespace_id=namespace_id,
        event_type=event_type,
        aggregate_type=aggregate_type,
        aggregate_id=str(aggregate_id),
        payload=payload,
        metadata=metadata,
        operation_id=getattr(audit_context, "operation_id", None),
        request_id=getattr(audit_context, "request_id", None),
        actor_id=getattr(audit_context, "actor_id", None),
        tag_id=tag_id,
        resource_type=resource_type,
        resource_id=resource_id,
    )


def create_outbox_event(**kwargs) -> OutboxEvent | None:
    event = build_outbox_event(**kwargs)
    if event is None:
        return None
    event.save()
    return event


def create_outbox_events(records: list[OutboxEvent | None]) -> None:
    events = [record for record in records if record is not None]
    if events:
        OutboxEvent.objects.bulk_create(events)
