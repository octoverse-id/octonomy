from __future__ import annotations

from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response

from octonomy.assignments.selectors import (
    assignments_for_tenant,
    filter_resource_tags,
    filter_tag_resources,
)
from octonomy.assignments.serializers import (
    AssignmentDeleteSerializer,
    AssignmentSerializer,
    AssignmentWriteSerializer,
    BulkAssignSerializer,
    BulkRemoveSerializer,
    ResourceReplaceSerializer,
    ResourceTagSerializer,
    TagResourceSerializer,
)
from octonomy.assignments.services import (
    assign_tag,
    bulk_assign_tags,
    bulk_remove_tags,
    remove_tag_assignment,
    replace_resource_tags,
)
from octonomy.core.api import api_view
from octonomy.core.audit import build_audit_context
from octonomy.core.auth import (
    GLOBAL_SCOPE,
    application_ids_from_request,
    request_authorizes_global_references,
    request_include_global,
    require_scopes,
)
from octonomy.core.pagination import OctonomyLimitOffsetPagination
from octonomy.core.responses import data_response
from octonomy.core.selectors import (
    application_filter_params,
    apply_application_filter,
    apply_namespace_filter,
)
from octonomy.core.serializers import response_serializer_context
from octonomy.core.versioning import usage_count_mode_for_request
from octonomy.tags.models import Tag
from octonomy.tags.selectors import apply_usage_counts
from octonomy.tags.serializers import TagSerializer


def require_tenant(request) -> str:
    if not request.tenant_id:
        raise ValidationError({"X-Tenant-ID": ["This header is required."]})
    return request.tenant_id


def scope_context_for_request(request):
    return getattr(request, "scope_context", GLOBAL_SCOPE)


def paginate(request, queryset, serializer_class):
    paginator = OctonomyLimitOffsetPagination()
    page = paginator.paginate_queryset(queryset, request)
    if serializer_class is ResourceTagSerializer:
        apply_usage_counts(
            [assignment.tag for assignment in page],
            scope_context_for_request(request),
            mode=usage_count_mode_for_request(request),
            application_ids=application_ids_from_request(request),
            include_global=request_include_global(request),
        )
    serializer = serializer_class(page, many=True, context=response_serializer_context(request))
    return paginator.get_paginated_response(serializer.data)


@extend_schema(
    methods=["POST"],
    request=AssignmentWriteSerializer,
    responses={200: AssignmentSerializer, 201: AssignmentSerializer},
)
@extend_schema(methods=["DELETE"], request=AssignmentDeleteSerializer, responses={204: None})
@require_scopes(post="tags:write", delete="tags:write")
@api_view(["POST", "DELETE"])
def assignment_collection(request):
    tenant_id = require_tenant(request)
    scope_context = scope_context_for_request(request)

    if request.method == "DELETE":
        serializer = AssignmentDeleteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        remove_tag_assignment(
            tenant_id=tenant_id,
            application_id=data["application_id"],
            tag_id=data["tag_id"],
            resource_type=data["resource_type"],
            resource_id=data["resource_id"],
            audit_context=build_audit_context(request),
            scope_context=scope_context,
        )
        return Response(status=status.HTTP_204_NO_CONTENT)

    serializer = AssignmentWriteSerializer(
        data=request.data,
        context={
            "tenant_id": tenant_id,
            "scope_context": scope_context,
            "include_global": request_authorizes_global_references(request),
        },
    )
    serializer.is_valid(raise_exception=True)
    data = serializer.validated_data
    result = assign_tag(
        tenant_id=tenant_id,
        application_id=data["application_id"],
        tag=data["tag"],
        resource_type=data["resource_type"],
        resource_id=data["resource_id"],
        assigned_by=data.get("assigned_by"),
        audit_context=build_audit_context(request, data.get("assigned_by")),
        scope_context=scope_context,
        include_global=request_authorizes_global_references(request),
    )
    response_status = status.HTTP_201_CREATED if result.created else status.HTTP_200_OK
    return data_response(
        AssignmentSerializer(result.assignment, context=response_serializer_context(request)).data,
        status=response_status,
    )


@extend_schema(
    methods=["POST"],
    request=BulkAssignSerializer,
    responses=AssignmentSerializer(many=True),
)
@require_scopes(post="tags:write")
@api_view(["POST"])
def bulk_assign(request):
    tenant_id = require_tenant(request)
    scope_context = scope_context_for_request(request)
    serializer = BulkAssignSerializer(
        data=request.data,
        context={
            "tenant_id": tenant_id,
            "scope_context": scope_context,
            "include_global": request_authorizes_global_references(request),
        },
    )
    serializer.is_valid(raise_exception=True)
    data = serializer.validated_data
    result = bulk_assign_tags(
        tenant_id=tenant_id,
        audit_context=build_audit_context(request, data.get("assigned_by")),
        scope_context=scope_context,
        include_global=request_authorizes_global_references(request),
        **data,
    )
    return data_response(
        {
            "created": result["created"],
            "existing": result["existing"],
            "skipped": result["skipped"],
            "assignments": AssignmentSerializer(
                result["assignments"],
                many=True,
                context=response_serializer_context(request),
            ).data,
        }
    )


@extend_schema(
    methods=["POST"],
    request=BulkRemoveSerializer,
    responses={200: OpenApiResponse(description="Bulk remove summary.")},
)
@require_scopes(post="tags:write")
@api_view(["POST"])
def bulk_remove(request):
    tenant_id = require_tenant(request)
    scope_context = scope_context_for_request(request)
    serializer = BulkRemoveSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    removed = bulk_remove_tags(
        tenant_id=tenant_id,
        audit_context=build_audit_context(request),
        scope_context=scope_context,
        **serializer.validated_data,
    )
    return data_response({"removed": removed})


@extend_schema(
    methods=["GET"],
    parameters=[
        OpenApiParameter("application_id", str, required=True),
        OpenApiParameter("include_inactive", bool, required=False),
        OpenApiParameter("type", str, required=False),
        OpenApiParameter("limit", int, required=False),
        OpenApiParameter("offset", int, required=False),
    ],
    responses=ResourceTagSerializer(many=True),
)
@extend_schema(
    methods=["POST"],
    request=ResourceReplaceSerializer,
    responses=ResourceTagSerializer(many=True),
)
@require_scopes(get="tags:read", post="tags:write")
@api_view(["GET", "POST"])
def resource_tags(request, resource_type, resource_id):
    tenant_id = require_tenant(request)
    scope_context = scope_context_for_request(request)

    if request.method == "GET":
        application_id = request.query_params.get("application_id")
        if not application_id:
            raise ValidationError({"application_id": ["This query parameter is required."]})
        include_global = request_include_global(request)
        queryset = assignments_for_tenant(
            tenant_id,
            scope_context,
            include_global=include_global,
        ).for_resource(
            application_id=application_id,
            resource_type=resource_type,
            resource_id=resource_id,
            scope_context=scope_context,
            include_global=include_global,
        )
        queryset = filter_resource_tags(queryset, request.query_params)
        return paginate(request, queryset, ResourceTagSerializer)

    serializer = ResourceReplaceSerializer(
        data={**request.data, "resource_type": resource_type, "resource_id": resource_id},
        context={
            "tenant_id": tenant_id,
            "scope_context": scope_context,
            "include_global": request_authorizes_global_references(request),
        },
    )
    serializer.is_valid(raise_exception=True)
    data = serializer.validated_data
    result = replace_resource_tags(
        tenant_id=tenant_id,
        audit_context=build_audit_context(request, data.get("assigned_by")),
        scope_context=scope_context,
        include_global=request_authorizes_global_references(request),
        **data,
    )
    tags = [assignment.tag for assignment in result["assignments"]]
    apply_usage_counts(
        tags,
        scope_context,
        mode=usage_count_mode_for_request(request),
        application_ids=application_ids_from_request(request),
        include_global=request_include_global(request),
    )
    return data_response(
        {
            "created": result["created"],
            "removed": result["removed"],
            "tags": TagSerializer(
                tags, many=True, context=response_serializer_context(request)
            ).data,
        }
    )


@extend_schema(
    parameters=[
        OpenApiParameter("application_id", str, required=False),
        OpenApiParameter("resource_type", str, required=False),
        OpenApiParameter("limit", int, required=False),
        OpenApiParameter("offset", int, required=False),
    ],
    responses=TagResourceSerializer(many=True),
)
@require_scopes(get="tags:read")
@api_view(["GET"])
def tag_resources(request, tag_id):
    tenant_id = require_tenant(request)
    scope_context = scope_context_for_request(request)
    include_global = request_include_global(request)
    application_ids, include_shared = application_filter_params(request)
    try:
        tag = apply_application_filter(
            apply_namespace_filter(
                Tag.objects.for_tenant(tenant_id),
                scope_context,
                include_global=include_global,
            ),
            application_ids,
            include_shared=include_shared,
        ).get(id=tag_id)
    except Tag.DoesNotExist:
        raise NotFound("Tag was not found.")

    queryset = filter_tag_resources(
        assignments_for_tenant(tenant_id, scope_context, include_global=include_global).filter(
            tag=tag
        ),
        request.query_params,
    )
    return paginate(request, queryset, TagResourceSerializer)
