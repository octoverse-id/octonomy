from __future__ import annotations

from django.db.models import Q, QuerySet

from octonomy.tags.models import Tag


def tags_for_tenant(tenant_id: str) -> QuerySet[Tag]:
    return Tag.objects.for_tenant(tenant_id)


def filter_tags(queryset: QuerySet[Tag], params) -> QuerySet[Tag]:
    application_id = params.get("application_id")
    include_shared = params.get("include_shared", "true").lower() != "false"

    if application_id and include_shared:
        queryset = queryset.filter(
            Q(application_id=application_id) | Q(application_id__isnull=True)
        )
    elif application_id:
        queryset = queryset.filter(application_id=application_id)

    for field in ("type", "slug", "parent_id"):
        value = params.get(field)
        if value:
            queryset = queryset.filter(**{field: value})

    is_active = params.get("is_active")
    if is_active is None:
        queryset = queryset.filter(is_active=True)
    else:
        queryset = queryset.filter(is_active=is_active.lower() == "true")

    q = params.get("q")
    if q:
        queryset = queryset.filter(Q(name__icontains=q) | Q(slug__icontains=q))

    return queryset
