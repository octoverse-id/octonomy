from __future__ import annotations

import uuid
from dataclasses import dataclass


@dataclass(frozen=True)
class AuditContext:
    actor_id: str | None
    request_id: str | None
    operation_id: uuid.UUID


def resolve_actor_id(request, fallback: str | None = None) -> str | None:
    actor_id = request.headers.get("X-Actor-ID")
    return actor_id or fallback or None


def build_audit_context(request, fallback_actor_id: str | None = None) -> AuditContext:
    return AuditContext(
        actor_id=resolve_actor_id(request, fallback_actor_id),
        request_id=getattr(request, "request_id", None),
        operation_id=uuid.uuid4(),
    )
