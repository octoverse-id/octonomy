from __future__ import annotations

from django.db.models import QuerySet

from octonomy.audit.models import AuditLog


def audit_logs_for_tenant(tenant_id: str) -> QuerySet[AuditLog]:
    return AuditLog.objects.for_tenant(tenant_id)


def filter_audit_logs(queryset: QuerySet[AuditLog], params) -> QuerySet[AuditLog]:
    for field in (
        "application_id",
        "action",
        "entity_type",
        "entity_id",
        "tag_id",
        "resource_type",
        "resource_id",
        "actor_id",
        "operation_id",
    ):
        value = params.get(field)
        if value:
            queryset = queryset.filter(**{field: value})
    return queryset
