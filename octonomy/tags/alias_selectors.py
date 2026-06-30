from __future__ import annotations

from django.db.models import Case, IntegerField, Q, QuerySet, Value, When
from rest_framework.fields import BooleanField

from octonomy.core.auth import GLOBAL_SCOPE, ScopeContext
from octonomy.core.selectors import apply_namespace_filter, namespace_q
from octonomy.tags.models import Tag, TagAlias


def aliases_for_tenant(
    tenant_id: str,
    scope_context: ScopeContext = GLOBAL_SCOPE,
    *,
    include_global: bool = True,
) -> QuerySet[TagAlias]:
    return apply_namespace_filter(
        TagAlias.objects.for_tenant(tenant_id),
        scope_context,
        include_global=include_global,
    ).select_related("tag")


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


def namespace_resolution_order(
    queryset: QuerySet,
    scope_context: ScopeContext,
    *tiebreakers: str,
) -> QuerySet:
    if scope_context.is_global:
        if tiebreakers:
            return queryset.order_by(*tiebreakers)
        return queryset
    return queryset.annotate(
        resolution_priority=Case(
            When(
                namespace_type=scope_context.namespace_type,
                namespace_id=scope_context.namespace_id,
                then=Value(0),
            ),
            default=Value(1),
            output_field=IntegerField(),
        )
    ).order_by("resolution_priority", *tiebreakers)


def filter_no_application_resolution(queryset: QuerySet, scope_context: ScopeContext) -> QuerySet:
    if scope_context.is_global:
        return queryset.filter(application_id__isnull=True)
    return queryset.filter(
        Q(
            namespace_type=scope_context.namespace_type,
            namespace_id=scope_context.namespace_id,
        )
        | Q(application_id__isnull=True)
    )


def active_tags_for_resolution(
    tenant_id: str,
    slug: str,
    application_id: str | None,
    tag_type: str | None = None,
    scope_context: ScopeContext = GLOBAL_SCOPE,
    *,
    include_global: bool = True,
) -> QuerySet[Tag]:
    queryset = apply_namespace_filter(
        Tag.objects.for_tenant(tenant_id).active().filter(slug=slug),
        scope_context,
        include_global=include_global,
    )
    if tag_type:
        queryset = queryset.filter(type=tag_type)
    if application_id:
        queryset = queryset.filter(
            Q(application_id=application_id) | Q(application_id__isnull=True)
        )
        if scope_context.is_global:
            return queryset.annotate(
                resolution_priority=Case(
                    When(application_id=application_id, then=Value(0)),
                    default=Value(1),
                    output_field=IntegerField(),
                )
            ).order_by("resolution_priority", "type", "name", "id")
        return queryset.annotate(
            resolution_priority=Case(
                When(
                    application_id=application_id,
                    namespace_type=scope_context.namespace_type,
                    namespace_id=scope_context.namespace_id,
                    then=Value(0),
                ),
                When(
                    application_id=application_id,
                    namespace_type__isnull=True,
                    namespace_id__isnull=True,
                    then=Value(1),
                ),
                default=Value(2),
                output_field=IntegerField(),
            )
        ).order_by("resolution_priority", "type", "name", "id")
    return namespace_resolution_order(
        filter_no_application_resolution(queryset, scope_context),
        scope_context,
        "type",
        "name",
        "id",
    )


def active_aliases_for_resolution(
    tenant_id: str,
    slug: str,
    application_id: str | None,
    scope_context: ScopeContext = GLOBAL_SCOPE,
    *,
    include_global: bool = True,
) -> QuerySet[TagAlias]:
    queryset = (
        aliases_for_tenant(tenant_id, scope_context, include_global=include_global)
        .active()
        .filter(slug=slug, tag__is_active=True)
        .filter(namespace_q(scope_context, include_global=include_global, prefix="tag__"))
    )
    if application_id:
        queryset = queryset.filter(
            Q(application_id=application_id) | Q(application_id__isnull=True)
        )
        # Resolution precedence is most-specific first:
        # (app, namespace) > (app, global namespace) > (tenant shared, global).
        if scope_context.is_global:
            return queryset.annotate(
                resolution_priority=Case(
                    When(application_id=application_id, then=Value(0)),
                    default=Value(1),
                    output_field=IntegerField(),
                )
            ).order_by("resolution_priority", "name", "id")
        return queryset.annotate(
            resolution_priority=Case(
                When(
                    application_id=application_id,
                    namespace_type=scope_context.namespace_type,
                    namespace_id=scope_context.namespace_id,
                    then=Value(0),
                ),
                When(
                    application_id=application_id,
                    namespace_type__isnull=True,
                    namespace_id__isnull=True,
                    then=Value(1),
                ),
                default=Value(2),
                output_field=IntegerField(),
            )
        ).order_by("resolution_priority", "name", "id")
    return namespace_resolution_order(
        filter_no_application_resolution(queryset, scope_context),
        scope_context,
        "name",
        "id",
    )


def active_aliases_for_resolution_bulk(
    tenant_id: str,
    slugs: list[str],
    application_id: str | None,
    scope_context: ScopeContext = GLOBAL_SCOPE,
    *,
    include_global: bool = True,
) -> QuerySet[TagAlias]:
    queryset = (
        aliases_for_tenant(tenant_id, scope_context, include_global=include_global)
        .active()
        .filter(
            slug__in=slugs,
            tag__is_active=True,
        )
        .filter(namespace_q(scope_context, include_global=include_global, prefix="tag__"))
    )
    if application_id:
        queryset = queryset.filter(
            Q(application_id=application_id) | Q(application_id__isnull=True)
        )
        if scope_context.is_global:
            return queryset.annotate(
                resolution_priority=Case(
                    When(application_id=application_id, then=Value(0)),
                    default=Value(1),
                    output_field=IntegerField(),
                )
            ).order_by("slug", "resolution_priority", "name", "id")
        return queryset.annotate(
            resolution_priority=Case(
                When(
                    application_id=application_id,
                    namespace_type=scope_context.namespace_type,
                    namespace_id=scope_context.namespace_id,
                    then=Value(0),
                ),
                When(
                    application_id=application_id,
                    namespace_type__isnull=True,
                    namespace_id__isnull=True,
                    then=Value(1),
                ),
                default=Value(2),
                output_field=IntegerField(),
            )
        ).order_by("slug", "resolution_priority", "name", "id")
    return namespace_resolution_order(
        filter_no_application_resolution(queryset, scope_context),
        scope_context,
        "slug",
        "name",
        "id",
    )
