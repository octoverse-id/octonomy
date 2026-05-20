from __future__ import annotations

from django.db.models import Count, Q, QuerySet

from octonomy.tags.models import Tag


def tags_for_tenant(tenant_id: str) -> QuerySet[Tag]:
    return Tag.objects.for_tenant(tenant_id).annotate(usage_count=Count("assignments"))


def apply_usage_counts(tags) -> None:
    tag_list = list(tags)
    tag_ids = [tag.id for tag in tag_list]
    counts = dict(
        Tag.objects.filter(id__in=tag_ids)
        .annotate(usage_count=Count("assignments"))
        .values_list("id", "usage_count")
    )
    for tag in tag_list:
        tag.usage_count = counts.get(tag.id, 0)


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
