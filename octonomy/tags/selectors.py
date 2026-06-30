from __future__ import annotations

from django.db.models import Count, Q, QuerySet

from octonomy.core.auth import GLOBAL_SCOPE, ScopeContext
from octonomy.core.selectors import apply_namespace_filter, namespace_q
from octonomy.tags.models import Tag


def usage_count_filter(
    scope_context: ScopeContext = GLOBAL_SCOPE,
    *,
    mode: str = "legacy",
) -> Q | None:
    if mode == "legacy":
        return None
    if mode != "visible":
        raise ValueError("usage count mode must be 'legacy' or 'visible'.")
    return namespace_q(scope_context, include_global=True, prefix="assignments__")


def tags_for_tenant(
    tenant_id: str,
    scope_context: ScopeContext = GLOBAL_SCOPE,
    *,
    include_global: bool = True,
    usage_count_mode: str = "legacy",
) -> QuerySet[Tag]:
    queryset = apply_namespace_filter(
        Tag.objects.for_tenant(tenant_id),
        scope_context,
        include_global=include_global,
    )
    count_filter = usage_count_filter(scope_context, mode=usage_count_mode)
    if count_filter is None:
        return queryset.annotate(usage_count=Count("assignments"))
    return queryset.annotate(usage_count=Count("assignments", filter=count_filter))


def apply_usage_counts(
    tags,
    scope_context: ScopeContext = GLOBAL_SCOPE,
    *,
    mode: str = "legacy",
) -> None:
    tag_list = list(tags)
    tag_ids = [tag.id for tag in tag_list]
    count_filter = usage_count_filter(scope_context, mode=mode)
    queryset = Tag.objects.filter(id__in=tag_ids)
    if count_filter is None:
        queryset = queryset.annotate(usage_count=Count("assignments"))
    else:
        queryset = queryset.annotate(usage_count=Count("assignments", filter=count_filter))
    counts = dict(queryset.values_list("id", "usage_count"))
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

    for field in ("type", "slug", "parent_id", "vocabulary_id"):
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
