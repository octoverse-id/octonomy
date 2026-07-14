from __future__ import annotations

from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.exceptions import ValidationError

from octonomy.audit.selectors import audit_logs_for_tenant, filter_audit_logs
from octonomy.audit.serializers import AuditLogSerializer
from octonomy.core.api import api_view
from octonomy.core.auth import GLOBAL_SCOPE, request_include_global, require_scopes
from octonomy.core.pagination import OctonomyLimitOffsetPagination
from octonomy.core.serializers import response_serializer_context


def require_tenant(request) -> str:
    if not request.tenant_id:
        raise ValidationError({"X-Tenant-ID": ["This header is required."]})
    return request.tenant_id


def scope_context_for_request(request):
    return getattr(request, "scope_context", GLOBAL_SCOPE)


def paginate(request, queryset):
    paginator = OctonomyLimitOffsetPagination()
    page = paginator.paginate_queryset(queryset, request)
    serializer = AuditLogSerializer(page, many=True, context=response_serializer_context(request))
    return paginator.get_paginated_response(serializer.data)


AUDIT_FILTER_PARAMETERS = [
    OpenApiParameter("application_id", str, required=False),
    OpenApiParameter("action", str, required=False),
    OpenApiParameter("entity_type", str, required=False),
    OpenApiParameter("entity_id", str, required=False),
    OpenApiParameter("tag_id", str, required=False),
    OpenApiParameter("resource_type", str, required=False),
    OpenApiParameter("resource_id", str, required=False),
    OpenApiParameter("actor_id", str, required=False),
    OpenApiParameter("operation_id", str, required=False),
    OpenApiParameter("limit", int, required=False),
    OpenApiParameter("offset", int, required=False),
]


@extend_schema(parameters=AUDIT_FILTER_PARAMETERS, responses=AuditLogSerializer(many=True))
@require_scopes(get="audit:read")
@api_view(["GET"])
def audit_logs_collection(request):
    tenant_id = require_tenant(request)
    queryset = filter_audit_logs(
        audit_logs_for_tenant(
            tenant_id,
            scope_context_for_request(request),
            include_global=request_include_global(request),
        ),
        request.query_params,
    )
    return paginate(request, queryset)


@extend_schema(
    parameters=[
        OpenApiParameter("action", str, required=False),
        OpenApiParameter("actor_id", str, required=False),
        OpenApiParameter("operation_id", str, required=False),
        OpenApiParameter("limit", int, required=False),
        OpenApiParameter("offset", int, required=False),
    ],
    responses=AuditLogSerializer(many=True),
)
@require_scopes(get="audit:read")
@api_view(["GET"])
def tag_audit_logs(request, tag_id):
    tenant_id = require_tenant(request)
    queryset = filter_audit_logs(
        audit_logs_for_tenant(
            tenant_id,
            scope_context_for_request(request),
            include_global=request_include_global(request),
        ).filter(tag_id=tag_id),
        request.query_params,
    )
    return paginate(request, queryset)


@extend_schema(
    parameters=[
        OpenApiParameter("application_id", str, required=False),
        OpenApiParameter("action", str, required=False),
        OpenApiParameter("actor_id", str, required=False),
        OpenApiParameter("operation_id", str, required=False),
        OpenApiParameter("limit", int, required=False),
        OpenApiParameter("offset", int, required=False),
    ],
    responses=AuditLogSerializer(many=True),
)
@require_scopes(get="audit:read")
@api_view(["GET"])
def resource_audit_logs(request, resource_type, resource_id):
    tenant_id = require_tenant(request)
    queryset = filter_audit_logs(
        audit_logs_for_tenant(
            tenant_id,
            scope_context_for_request(request),
            include_global=request_include_global(request),
        ).filter(
            resource_type=resource_type,
            resource_id=resource_id,
        ),
        request.query_params,
    )
    return paginate(request, queryset)
