from __future__ import annotations

from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response

from octonomy.core.audit import build_audit_context
from octonomy.core.auth import GLOBAL_SCOPE, require_scopes
from octonomy.core.pagination import OctonomyLimitOffsetPagination
from octonomy.core.responses import data_response
from octonomy.tags.selectors import apply_usage_counts, filter_tags, tags_for_tenant
from octonomy.tags.serializers import TagPatchSerializer, TagSerializer, TagWriteSerializer
from octonomy.tags.services import create_tag, deactivate_tag, update_tag


def require_tenant(request) -> str:
    if not request.tenant_id:
        raise ValidationError({"X-Tenant-ID": ["This header is required."]})
    return request.tenant_id


def scope_context_for_request(request):
    return getattr(request, "scope_context", GLOBAL_SCOPE)


def get_tag_or_404(tenant_id: str, tag_id, scope_context=GLOBAL_SCOPE) -> object:
    try:
        return tags_for_tenant(tenant_id, scope_context).get(id=tag_id)
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

    if request.method == "GET":
        scope_context = scope_context_for_request(request)
        queryset = filter_tags(tags_for_tenant(tenant_id, scope_context), request.query_params)
        paginator = OctonomyLimitOffsetPagination()
        page = paginator.paginate_queryset(queryset, request)
        serializer = TagSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)

    scope_context = scope_context_for_request(request)
    serializer = TagWriteSerializer(
        data=request.data,
        context={"tenant_id": tenant_id, "scope_context": scope_context},
    )
    serializer.is_valid(raise_exception=True)
    tag = create_tag(tenant_id, serializer.validated_data, build_audit_context(request))
    apply_usage_counts([tag])
    return data_response(TagSerializer(tag).data, status=status.HTTP_201_CREATED)


@extend_schema(methods=["GET"], responses=TagSerializer)
@extend_schema(methods=["PATCH"], request=TagPatchSerializer, responses=TagSerializer)
@extend_schema(methods=["DELETE"], responses={204: None})
@require_scopes(get="tags:read", patch="tags:write", delete="tags:write")
@api_view(["GET", "PATCH", "DELETE"])
def tag_detail(request, tag_id):
    tenant_id = require_tenant(request)
    scope_context = scope_context_for_request(request)
    tag = get_tag_or_404(tenant_id, tag_id, scope_context)

    if request.method == "GET":
        apply_usage_counts([tag])
        return data_response(TagSerializer(tag).data)

    if request.method == "DELETE":
        deactivate_tag(tag, build_audit_context(request))
        return Response(status=status.HTTP_204_NO_CONTENT)

    serializer = TagPatchSerializer(
        data=request.data,
        partial=True,
        context={"tenant_id": tenant_id, "scope_context": scope_context},
    )
    serializer.is_valid(raise_exception=True)
    tag = update_tag(tag, serializer.validated_data, build_audit_context(request))
    apply_usage_counts([tag])
    return data_response(TagSerializer(tag).data)
