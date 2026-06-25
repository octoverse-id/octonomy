from __future__ import annotations

from django.db.models import QuerySet

from octonomy.assignments.models import TagAssignment


def assignments_for_tenant(tenant_id: str) -> QuerySet[TagAssignment]:
    return TagAssignment.objects.for_tenant(tenant_id).global_scope().select_related("tag")


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
