from __future__ import annotations

from django.db.models import Q, QuerySet

from octonomy.core.auth import GLOBAL_SCOPE, ScopeContext
from octonomy.core.selectors import apply_namespace_filter
from octonomy.tags.models import Vocabulary


def vocabularies_for_tenant(
    tenant_id: str,
    scope_context: ScopeContext = GLOBAL_SCOPE,
    *,
    include_global: bool = True,
) -> QuerySet[Vocabulary]:
    return apply_namespace_filter(
        Vocabulary.objects.for_tenant(tenant_id),
        scope_context,
        include_global=include_global,
    )


def filter_vocabularies(queryset: QuerySet[Vocabulary], params) -> QuerySet[Vocabulary]:
    application_id = params.get("application_id")
    include_shared = params.get("include_shared", "true").lower() != "false"

    if application_id and include_shared:
        queryset = queryset.filter(
            Q(application_id=application_id) | Q(application_id__isnull=True)
        )
    elif application_id:
        queryset = queryset.filter(application_id=application_id)

    for field in ("slug",):
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
