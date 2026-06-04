from __future__ import annotations

from django.db.models import Case, IntegerField, Q, QuerySet, Value, When
from rest_framework.fields import BooleanField

from octonomy.tags.models import Tag, TagAlias


def aliases_for_tenant(tenant_id: str) -> QuerySet[TagAlias]:
    return TagAlias.objects.for_tenant(tenant_id).select_related("tag")


def filter_aliases(queryset: QuerySet[TagAlias], params) -> QuerySet[TagAlias]:
    application_id = params.get("application_id")
    include_shared = BooleanField().to_internal_value(params.get("include_shared", True))

    if application_id and include_shared:
        queryset = queryset.filter(
            Q(application_id=application_id) | Q(application_id__isnull=True)
        )
    elif application_id:
        queryset = queryset.filter(application_id=application_id)

    for field in ("tag_id", "slug"):
        value = params.get(field)
        if value:
            queryset = queryset.filter(**{field: value})

    is_active = params.get("is_active")
    if is_active is None:
        queryset = queryset.filter(is_active=True)
    else:
        queryset = queryset.filter(is_active=BooleanField().to_internal_value(is_active))

    q = params.get("q")
    if q:
        queryset = queryset.filter(Q(name__icontains=q) | Q(slug__icontains=q))

    return queryset


def active_tags_for_resolution(
    tenant_id: str,
    slug: str,
    application_id: str | None,
    tag_type: str | None = None,
) -> QuerySet[Tag]:
    queryset = Tag.objects.for_tenant(tenant_id).active().filter(slug=slug)
    if tag_type:
        queryset = queryset.filter(type=tag_type)
    if application_id:
        return queryset.filter(Q(application_id=application_id) | Q(application_id__isnull=True))
    return queryset.filter(application_id__isnull=True)


def active_aliases_for_resolution(
    tenant_id: str,
    slug: str,
    application_id: str | None,
) -> QuerySet[TagAlias]:
    queryset = aliases_for_tenant(tenant_id).active().filter(slug=slug, tag__is_active=True)
    if application_id:
        # App-specific aliases should shadow shared aliases during resolution,
        # while still allowing shared aliases to act as tenant-wide fallbacks.
        return (
            queryset.filter(Q(application_id=application_id) | Q(application_id__isnull=True))
            .annotate(
                scope_priority=Case(
                    When(application_id=application_id, then=Value(0)),
                    default=Value(1),
                    output_field=IntegerField(),
                )
            )
            .order_by("scope_priority", "name", "id")
        )
    return queryset.filter(application_id__isnull=True)


def active_aliases_for_resolution_bulk(
    tenant_id: str,
    slugs: list[str],
    application_id: str | None,
) -> QuerySet[TagAlias]:
    queryset = (
        aliases_for_tenant(tenant_id)
        .active()
        .filter(
            slug__in=slugs,
            tag__is_active=True,
        )
    )
    if application_id:
        # Keep bulk alias resolution ordered the same way as single resolution so
        # repeated alias slugs consistently choose app-specific aliases first.
        return (
            queryset.filter(Q(application_id=application_id) | Q(application_id__isnull=True))
            .annotate(
                scope_priority=Case(
                    When(application_id=application_id, then=Value(0)),
                    default=Value(1),
                    output_field=IntegerField(),
                )
            )
            .order_by("slug", "scope_priority", "name", "id")
        )
    return queryset.filter(application_id__isnull=True).order_by("slug", "name", "id")
