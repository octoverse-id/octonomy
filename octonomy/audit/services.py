from __future__ import annotations

import logging
from typing import Any

from octonomy.audit.models import AuditLog
from octonomy.core.audit import AuditContext

logger = logging.getLogger(__name__)


def tag_snapshot(tag) -> dict[str, Any]:
    return {
        "id": str(tag.id),
        "tenant_id": tag.tenant_id,
        "application_id": tag.application_id,
        "name": tag.name,
        "slug": tag.slug,
        "type": tag.type,
        "description": tag.description,
        "parent_id": str(tag.parent_id) if tag.parent_id else None,
        "metadata": tag.metadata,
        "is_active": tag.is_active,
        "created_at": tag.created_at.isoformat() if tag.created_at else None,
        "updated_at": tag.updated_at.isoformat() if tag.updated_at else None,
    }


def assignment_snapshot(assignment) -> dict[str, Any]:
    return {
        "id": str(assignment.id),
        "tenant_id": assignment.tenant_id,
        "application_id": assignment.application_id,
        "tag_id": str(assignment.tag_id),
        "resource_type": assignment.resource_type,
        "resource_id": assignment.resource_id,
        "assigned_by": assignment.assigned_by,
        "assigned_at": assignment.assigned_at.isoformat() if assignment.assigned_at else None,
    }


def build_audit_log(
    *,
    tenant_id: str,
    action: str,
    entity_type: str,
    entity_id: str,
    audit_context: AuditContext | None,
    application_id: str | None = None,
    tag_id=None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    changes: dict | None = None,
    metadata: dict | None = None,
) -> AuditLog | None:
    if audit_context is None:
        logger.warning(
            "Audit log skipped: no audit_context for action=%s entity_type=%s entity_id=%s",
            action,
            entity_type,
            entity_id,
        )
        return None

    return AuditLog(
        tenant_id=tenant_id,
        application_id=application_id,
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id),
        tag_id=tag_id,
        resource_type=resource_type,
        resource_id=resource_id,
        actor_id=audit_context.actor_id,
        request_id=audit_context.request_id,
        operation_id=audit_context.operation_id,
        changes=changes or {},
        metadata=metadata or {},
    )


def create_audit_log(**kwargs) -> AuditLog | None:
    audit_log = build_audit_log(**kwargs)
    if audit_log is None:
        return None
    audit_log.save()
    return audit_log


def create_audit_logs(records: list[AuditLog]) -> None:
    if records:
        AuditLog.objects.bulk_create(records)
