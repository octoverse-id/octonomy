from __future__ import annotations

from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response

from octonomy.core.api import api_view
from octonomy.core.audit import build_audit_context
from octonomy.core.auth import (
    GLOBAL_SCOPE,
    application_ids_from_request,
    request_include_global,
    require_scopes,
)
from octonomy.core.pagination import OctonomyLimitOffsetPagination
from octonomy.core.responses import data_response
from octonomy.core.selectors import (
    application_filter_params,
    apply_application_filter,
    create_payload_with_scope,
    reject_null_namespaced_application_id,
    scoped_create_data,
)
from octonomy.core.versioning import usage_count_mode_for_request
from octonomy.tags.selectors import apply_usage_counts, filter_tags, tags_for_tenant
from octonomy.tags.serializers import TagPatchSerializer, TagSerializer, TagWriteSerializer
from octonomy.tags.services import create_tag, deactivate_tag, update_tag


def require_tenant(request) -> str:
    if not request.tenant_id:
        raise ValidationError({"X-Tenant-ID": ["This header is required."]})
    return request.tenant_id


def scope_context_for_request(request):
    return getattr(request, "scope_context", GLOBAL_SCOPE)


def get_tag_or_404(
    tenant_id: str,
    tag_id,
    scope_context=GLOBAL_SCOPE,
    *,
    include_global: bool = True,
    application_ids=None,
    include_shared: bool = True,
) -> object:
    try:
        queryset = apply_application_filter(
            tags_for_tenant(tenant_id, scope_context, include_global=include_global),
            application_ids,
            include_shared=include_shared,
        )
        return queryset.get(id=tag_id)
    except Exception:
        raise NotFound("Tag was not found.")


@extend_schema(
    methods=["GET"],
    parameters=[
        OpenApiParameter("application_id", str, required=False),
        OpenApiParameter("include_shared", bool, required=False),
        OpenApiParameter("type", str, required=False),
        OpenApiParameter("slug", str, required=False),
        OpenApiParameter("parent_id", str, required=False),
        OpenApiParameter("vocabulary_id", str, required=False),
        OpenApiParameter("is_active", bool, required=False),
        OpenApiParameter("q", str, required=False),
        OpenApiParameter("limit", int, required=False),
        OpenApiParameter("offset", int, required=False),
    ],
    responses=TagSerializer(many=True),
)
@extend_schema(methods=["POST"], request=TagWriteSerializer, responses={201: TagSerializer})
@require_scopes(get="tags:read", post="tags:write")
@api_view(["GET", "POST"])
def tags_collection(request):
    tenant_id = require_tenant(request)

    scope_context = scope_context_for_request(request)
    usage_count_mode = usage_count_mode_for_request(request)
    count_application_ids = application_ids_from_request(request)

    if request.method == "GET":
        queryset = filter_tags(
            tags_for_tenant(
                tenant_id,
                scope_context,
                include_global=request_include_global(request),
                usage_count_mode=usage_count_mode,
                application_ids=count_application_ids,
            ),
            request.query_params,
        )
        paginator = OctonomyLimitOffsetPagination()
        page = paginator.paginate_queryset(queryset, request)
        serializer = TagSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)

    serializer = TagWriteSerializer(
        data=create_payload_with_scope(request, scope_context),
        context={"tenant_id": tenant_id, "scope_context": scope_context},
    )
    serializer.is_valid(raise_exception=True)
    # Persist the request's namespace on create so an enabled namespaced write
    # lands in the caller's scope; for global requests this injects nulls.
    tag = create_tag(
        tenant_id,
        scoped_create_data(serializer, scope_context),
        build_audit_context(request),
    )
    apply_usage_counts(
        [tag],
        scope_context,
        mode=usage_count_mode,
        application_ids=count_application_ids,
        include_global=request_include_global(request),
    )
    return data_response(TagSerializer(tag).data, status=status.HTTP_201_CREATED)


@extend_schema(methods=["GET"], responses=TagSerializer)
@extend_schema(methods=["PATCH"], request=TagPatchSerializer, responses=TagSerializer)
@extend_schema(methods=["DELETE"], responses={204: None})
@require_scopes(get="tags:read", patch="tags:write", delete="tags:write")
@api_view(["GET", "PATCH", "DELETE"])
def tag_detail(request, tag_id):
    tenant_id = require_tenant(request)
    scope_context = scope_context_for_request(request)
    # Reads may fall back to global rows when authorized; writes (PATCH/DELETE)
    # must target the request's exact scope so a namespaced caller can never
    # mutate or deactivate a tenant-wide row.
    include_global = request_include_global(request) if request.method == "GET" else False
    usage_count_mode = usage_count_mode_for_request(request)
    count_application_ids = application_ids_from_request(request)
    application_ids, include_shared = application_filter_params(request)
    tag = get_tag_or_404(
        tenant_id,
        tag_id,
        scope_context,
        include_global=include_global,
        application_ids=application_ids,
        include_shared=include_shared,
    )

    if request.method == "GET":
        apply_usage_counts(
            [tag],
            scope_context,
            mode=usage_count_mode,
            application_ids=count_application_ids,
            include_global=include_global,
        )
        return data_response(TagSerializer(tag).data)

    if request.method == "DELETE":
        deactivate_tag(tag, build_audit_context(request))
        return Response(status=status.HTTP_204_NO_CONTENT)

    reject_null_namespaced_application_id(request.data, scope_context)
    serializer = TagPatchSerializer(
        data=request.data,
        partial=True,
        context={"tenant_id": tenant_id, "scope_context": scope_context},
    )
    serializer.is_valid(raise_exception=True)
    tag = update_tag(tag, serializer.validated_data, build_audit_context(request))
    apply_usage_counts(
        [tag],
        scope_context,
        mode=usage_count_mode,
        application_ids=count_application_ids,
        include_global=include_global,
    )
    return data_response(TagSerializer(tag).data)
