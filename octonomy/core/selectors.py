from __future__ import annotations

from django.db.models import Q, QuerySet

from octonomy.core.auth import GLOBAL_SCOPE, ScopeContext, authorized_application_ids


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
    """Bound rows to the applications a caller is authorized for.

    Object-by-id lookups must not return a row in an application the caller is not
    authorized for — a ``(commerce, merchant_a)`` grant must not reach a
    ``(cms, merchant_a)`` row that merely shares the namespace. ``application_ids``
    is that authorized set; ``None`` means unrestricted (a tenant-wide grant), so a
    PATCH may still fetch the row it is moving to another application.
    ``include_shared`` keeps application-shared rows (``application_id IS NULL``)
    visible, matching the collection endpoints.
    """

    if application_ids is None:
        return queryset
    ids = [application_id for application_id in application_ids if application_id]
    application_q = Q(application_id__in=ids)
    if include_shared:
        application_q |= Q(application_id__isnull=True)
    return queryset.filter(application_q)


def application_filter_params(request) -> tuple[set[str] | None, bool]:
    """Authorized application scope + ``include_shared`` for an object-by-id lookup.

    The scope is the applications the caller is granted for the request's namespace
    (``authorized_application_ids``), not the request-named ``application_id``: on a
    PATCH move the body names the destination, so filtering by it would 404 the
    source row before ``update_tag`` can validate the move.
    """

    include_shared = request.query_params.get("include_shared", "true").lower() != "false"
    return authorized_application_ids(request), include_shared


def namespace_kwargs(scope_context: ScopeContext = GLOBAL_SCOPE) -> dict[str, str | None]:
    return {
        "namespace_type": scope_context.namespace_type,
        "namespace_id": scope_context.namespace_id,
    }


def scoped_create_data(serializer, request, scope_context: ScopeContext) -> dict:
    """Build create data carrying the request's namespace and application scope.

    A namespaced row requires a non-null ``application_id``. Authorization accepts
    ``application_id`` from the query string as well as the body, so when the body
    omits it, fall back to the query value rather than persisting ``NULL`` — which
    the namespace check would otherwise reject as a misleading duplicate/conflict.
    Global creates keep the body's value (``NULL`` = shared is valid there).
    """

    data = {**serializer.validated_data, **namespace_kwargs(scope_context)}
    if not scope_context.is_global and not data.get("application_id"):
        query_application_id = request.query_params.get("application_id")
        if query_application_id:
            data["application_id"] = query_application_id
    return data


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
