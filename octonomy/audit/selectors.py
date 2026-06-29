from __future__ import annotations

from django.db.models import QuerySet

from octonomy.audit.models import AuditLog
from octonomy.core.auth import GLOBAL_SCOPE, ScopeContext
from octonomy.core.selectors import apply_namespace_filter


def audit_logs_for_tenant(
    tenant_id: str,
    scope_context: ScopeContext = GLOBAL_SCOPE,
    *,
    include_global: bool = True,
) -> QuerySet[AuditLog]:
    return apply_namespace_filter(
        AuditLog.objects.for_tenant(tenant_id),
        scope_context,
        include_global=include_global,
    )


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
