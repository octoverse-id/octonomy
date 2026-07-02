from __future__ import annotations

from django.db.models import Q, QuerySet

from octonomy.core.auth import GLOBAL_SCOPE, ScopeContext, application_ids_from_request


def namespace_q(
    scope_context: ScopeContext = GLOBAL_SCOPE,
    *,
    include_global: bool = False,
    prefix: str = "",
) -> Q:
    namespace_type = f"{prefix}namespace_type"
    namespace_id = f"{prefix}namespace_id"
    global_q = Q(**{f"{namespace_type}__isnull": True, f"{namespace_id}__isnull": True})
    if scope_context.is_global:
        return global_q

    exact_q = Q(
        **{
            namespace_type: scope_context.namespace_type,
            namespace_id: scope_context.namespace_id,
        }
    )
    if include_global:
        return exact_q | global_q
    return exact_q


def apply_namespace_filter(
    queryset: QuerySet,
    scope_context: ScopeContext = GLOBAL_SCOPE,
    *,
    include_global: bool = False,
) -> QuerySet:
    return queryset.filter(namespace_q(scope_context, include_global=include_global))


def apply_application_filter(
    queryset: QuerySet,
    application_ids,
    *,
    include_shared: bool = True,
) -> QuerySet:
    """Constrain rows to the request's authorized application(s).

    Object-by-id lookups authorize the request's ``application_id`` but must also
    bound the fetched row to it: a ``(commerce, merchant_a)`` grant must not reach
    a ``(cms, merchant_a)`` row that merely shares the namespace. Accepts the set
    of application ids the request named (query and body, as authorization sees
    them). ``include_shared`` keeps application-shared rows (``application_id IS
    NULL``) visible, matching the collection endpoints.
    """

    ids = _application_id_list(application_ids)
    if not ids:
        return queryset
    application_q = Q(application_id__in=ids)
    if include_shared:
        application_q |= Q(application_id__isnull=True)
    return queryset.filter(application_q)


def _application_id_list(application_ids) -> list[str]:
    if application_ids is None:
        return []
    if isinstance(application_ids, str):
        application_ids = [application_ids]
    return [application_id for application_id in application_ids if application_id]


def application_filter_params(request) -> tuple[set[str], bool]:
    """Application scope + ``include_shared`` for an object-by-id lookup.

    Reads the same query+body ``application_id`` that authorization consumes
    (``application_ids_from_request``), so a body-only ``application_id`` on a
    PATCH/DELETE still bounds the fetched row rather than slipping past the query
    param the filter used to read.
    """

    include_shared = request.query_params.get("include_shared", "true").lower() != "false"
    return application_ids_from_request(request), include_shared


def namespace_kwargs(scope_context: ScopeContext = GLOBAL_SCOPE) -> dict[str, str | None]:
    return {
        "namespace_type": scope_context.namespace_type,
        "namespace_id": scope_context.namespace_id,
    }


def scope_context_from_values(
    namespace_type: str | None,
    namespace_id: str | None,
) -> ScopeContext:
    return ScopeContext(namespace_type=namespace_type, namespace_id=namespace_id)


def scope_context_from_instance_data(instance, data: dict) -> ScopeContext:
    return ScopeContext(
        namespace_type=data.get("namespace_type", instance.namespace_type),
        namespace_id=data.get("namespace_id", instance.namespace_id),
    )


def row_matches_scope(row, scope_context: ScopeContext, *, include_global: bool = False) -> bool:
    row_is_global = row.namespace_type is None and row.namespace_id is None
    if scope_context.is_global:
        return row_is_global
    if (
        row.namespace_type == scope_context.namespace_type
        and row.namespace_id == scope_context.namespace_id
    ):
        return True
    return include_global and row_is_global


def namespace_changed(instance, data: dict) -> bool:
    return (
        "namespace_type" in data
        and data["namespace_type"] != instance.namespace_type
        or "namespace_id" in data
        and data["namespace_id"] != instance.namespace_id
    )
