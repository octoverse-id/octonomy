from __future__ import annotations

from django.db.models import Q, QuerySet
from rest_framework import serializers

from octonomy.core.auth import (
    GLOBAL_SCOPE,
    ScopeContext,
    application_ids_from_request,
    authorized_application_ids,
)

# Only a move-capable method (PATCH) may look up a row outside the request-named
# application: its body names the *destination* app, so the source row must be
# found by authorized scope. Reads and deletes act on the current row and stay
# bound to the application the request named, consistent with list filtering.
_MOVE_CAPABLE_METHODS = frozenset({"PATCH", "PUT"})


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
    """Application scope + ``include_shared`` for an object-by-id lookup.

    Reads and deletes bound the fetched row to the application the request named
    (``application_ids_from_request``), staying consistent with list filtering and
    the "namespace below application" contract — a request for ``application_id=cms``
    must not return a ``commerce`` row. A PATCH may be an application move whose body
    names the *destination*, so its source lookup uses ``authorized_application_ids``
    (the apps the grant covers for the namespace) instead. ``None`` means no
    application filter (a tenant-wide request that named no application).
    """

    include_shared = request.query_params.get("include_shared", "true").lower() != "false"
    if request.method in _MOVE_CAPABLE_METHODS:
        return authorized_application_ids(request), include_shared
    return (application_ids_from_request(request) or None), include_shared


def namespace_kwargs(scope_context: ScopeContext = GLOBAL_SCOPE) -> dict[str, str | None]:
    return {
        "namespace_type": scope_context.namespace_type,
        "namespace_id": scope_context.namespace_id,
    }


def create_payload_with_scope(request, scope_context: ScopeContext):
    """Create-request body carrying the request's application scope.

    A namespaced row requires a non-null ``application_id``. Authorization accepts
    ``application_id`` from the query string as well as the body, so when the body
    omits the key, fold the query value into the serializer input — this way it
    passes the serializer's ``application_id`` validation (blank/whitespace and
    length) exactly like a body value. Global creates are untouched (``NULL`` =
    shared is valid there). The original body is returned unchanged when no
    injection is needed, preserving its type (a form/multipart ``QueryDict`` must
    not be flattened into a plain dict, which would turn each field into a list).
    """

    data = request.data
    if scope_context.is_global or not isinstance(data, dict):
        return data

    if "application_id" in data:
        # An explicit value reaches serializer validation. Blank/other values are
        # left to the serializer; an explicit null is rejected here (see below).
        reject_null_namespaced_application_id(data, scope_context)
        return data

    query_application_id = request.query_params.get("application_id")
    if not query_application_id:
        return data
    # copy() preserves the payload type (QueryDict for form/multipart) instead of
    # collapsing multi-value fields the way dict unpacking would.
    payload = data.copy()
    payload["application_id"] = query_application_id
    return payload


def reject_null_namespaced_application_id(data, scope_context: ScopeContext) -> None:
    """Reject an explicit null ``application_id`` for a namespaced write.

    A namespaced row requires a non-null ``application_id`` (isolation sits below
    application), but the serializers allow ``null`` because it is valid for a
    global row. The serializer cannot tell the two apart, so guard it here for
    namespaced create/update paths — returning a clear ``400`` instead of letting
    the database namespace check surface a misleading conflict.
    """

    if scope_context.is_global or not isinstance(data, dict):
        return
    if "application_id" in data and data.get("application_id") is None:
        raise serializers.ValidationError(
            {"application_id": ["This field is required for namespaced writes."]}
        )


def scoped_create_data(serializer, scope_context: ScopeContext) -> dict:
    """Merge validated create data with the request's namespace scope."""

    return {**serializer.validated_data, **namespace_kwargs(scope_context)}


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
