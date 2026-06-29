from __future__ import annotations

from django.db.models import QuerySet

from octonomy.assignments.models import TagAssignment
from octonomy.core.auth import GLOBAL_SCOPE, ScopeContext
from octonomy.core.selectors import apply_namespace_filter


def assignments_for_tenant(
    tenant_id: str,
    scope_context: ScopeContext = GLOBAL_SCOPE,
    *,
    include_global: bool = False,
) -> QuerySet[TagAssignment]:
    return apply_namespace_filter(
        TagAssignment.objects.for_tenant(tenant_id),
        scope_context,
        include_global=include_global,
    ).select_related("tag")


def filter_tag_resources(queryset: QuerySet[TagAssignment], params) -> QuerySet[TagAssignment]:
    application_id = params.get("application_id")
    resource_type = params.get("resource_type")
    if application_id:
        queryset = queryset.filter(application_id=application_id)
    if resource_type:
        queryset = queryset.filter(resource_type=resource_type)
    return queryset


def filter_resource_tags(queryset: QuerySet[TagAssignment], params) -> QuerySet[TagAssignment]:
    include_inactive = params.get("include_inactive", "false").lower() == "true"
    tag_type = params.get("type")
    if not include_inactive:
        queryset = queryset.filter(tag__is_active=True)
    if tag_type:
        queryset = queryset.filter(tag__type=tag_type)
    return queryset
