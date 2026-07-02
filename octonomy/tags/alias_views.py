from __future__ import annotations

from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response

from octonomy.core.api import api_view
from octonomy.core.audit import build_audit_context
from octonomy.core.auth import GLOBAL_SCOPE, request_include_global, require_scopes
from octonomy.core.pagination import OctonomyLimitOffsetPagination
from octonomy.core.responses import data_response
from octonomy.core.selectors import (
    application_filter_params,
    apply_application_filter,
    apply_namespace_filter,
    scoped_create_data,
)
from octonomy.core.validators import validate_external_id, validate_slug_like
from octonomy.core.versioning import usage_count_mode_for_request
from octonomy.tags.alias_selectors import aliases_for_tenant, filter_aliases
from octonomy.tags.alias_serializers import (
    TagAliasPatchSerializer,
    TagAliasSerializer,
    TagAliasWriteSerializer,
    TagResolutionSerializer,
)
from octonomy.tags.alias_services import (
    create_tag_alias,
    deactivate_tag_alias,
    resolve_tag_reference,
    update_tag_alias,
)
from octonomy.tags.models import Tag, TagAlias
from octonomy.tags.selectors import apply_usage_counts


def require_tenant(request) -> str:
    if not request.tenant_id:
        raise ValidationError({"X-Tenant-ID": ["This header is required."]})
    return request.tenant_id


def scope_context_for_request(request):
    return getattr(request, "scope_context", GLOBAL_SCOPE)


def get_alias_or_404(
    tenant_id: str,
    alias_id,
    scope_context=GLOBAL_SCOPE,
    *,
    include_global: bool = True,
    application_ids=None,
    include_shared: bool = True,
):
    try:
        queryset = apply_application_filter(
            aliases_for_tenant(tenant_id, scope_context, include_global=include_global),
            application_ids,
            include_shared=include_shared,
        )
        return queryset.get(id=alias_id)
    except TagAlias.DoesNotExist:
        raise NotFound("Tag alias was not found.")


def maybe_validate_application_id(request) -> str | None:
    application_id = request.query_params.get("application_id")
    if application_id is not None:
        validate_external_id(application_id, "application_id")
    return application_id


@extend_schema(
    methods=["GET"],
    parameters=[
        OpenApiParameter("application_id", str, required=False),
        OpenApiParameter("include_shared", bool, required=False),
        OpenApiParameter("tag_id", str, required=False),
        OpenApiParameter("slug", str, required=False),
        OpenApiParameter("is_active", bool, required=False),
        OpenApiParameter("q", str, required=False),
        OpenApiParameter("limit", int, required=False),
        OpenApiParameter("offset", int, required=False),
    ],
    responses=TagAliasSerializer(many=True),
)
@extend_schema(
    methods=["POST"],
    request=TagAliasWriteSerializer,
    responses={201: TagAliasSerializer},
)
@require_scopes(get="tags:read", post="tags:write")
@api_view(["GET", "POST"])
def aliases_collection(request):
    tenant_id = require_tenant(request)
    scope_context = scope_context_for_request(request)

    if request.method == "GET":
        maybe_validate_application_id(request)
        queryset = filter_aliases(
            aliases_for_tenant(
                tenant_id, scope_context, include_global=request_include_global(request)
            ),
            request.query_params,
        )
        paginator = OctonomyLimitOffsetPagination()
        page = paginator.paginate_queryset(queryset, request)
        serializer = TagAliasSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)

    serializer = TagAliasWriteSerializer(
        data=request.data,
        context={"tenant_id": tenant_id, "scope_context": scope_context},
    )
    serializer.is_valid(raise_exception=True)
    alias = create_tag_alias(
        tenant_id,
        scoped_create_data(serializer, request, scope_context),
        build_audit_context(request),
    )
    return data_response(TagAliasSerializer(alias).data, status=status.HTTP_201_CREATED)


@extend_schema(methods=["GET"], responses=TagAliasSerializer)
@extend_schema(methods=["PATCH"], request=TagAliasPatchSerializer, responses=TagAliasSerializer)
@extend_schema(methods=["DELETE"], responses={204: None})
@require_scopes(get="tags:read", patch="tags:write", delete="tags:write")
@api_view(["GET", "PATCH", "DELETE"])
def alias_detail(request, alias_id):
    tenant_id = require_tenant(request)
    scope_context = scope_context_for_request(request)
    # Writes (PATCH/DELETE) target the exact request scope; reads may fall back
    # to global rows only when the caller is authorized for the global namespace.
    include_global = request_include_global(request) if request.method == "GET" else False
    application_ids, include_shared = application_filter_params(request)
    alias = get_alias_or_404(
        tenant_id,
        alias_id,
        scope_context,
        include_global=include_global,
        application_ids=application_ids,
        include_shared=include_shared,
    )

    if request.method == "GET":
        return data_response(TagAliasSerializer(alias).data)

    if request.method == "DELETE":
        deactivate_tag_alias(alias, build_audit_context(request))
        return Response(status=status.HTTP_204_NO_CONTENT)

    serializer = TagAliasPatchSerializer(
        data=request.data,
        partial=True,
        context={"tenant_id": tenant_id, "scope_context": scope_context},
    )
    serializer.is_valid(raise_exception=True)
    alias = update_tag_alias(alias, serializer.validated_data, build_audit_context(request))
    return data_response(TagAliasSerializer(alias).data)


@extend_schema(
    parameters=[
        OpenApiParameter("application_id", str, required=False),
        OpenApiParameter("include_shared", bool, required=False),
        OpenApiParameter("is_active", bool, required=False),
        OpenApiParameter("limit", int, required=False),
        OpenApiParameter("offset", int, required=False),
    ],
    responses=TagAliasSerializer(many=True),
)
@require_scopes(get="tags:read")
@api_view(["GET"])
def tag_aliases(request, tag_id):
    tenant_id = require_tenant(request)
    scope_context = scope_context_for_request(request)
    include_global = request_include_global(request)
    maybe_validate_application_id(request)
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

    queryset = filter_aliases(
        aliases_for_tenant(tenant_id, scope_context, include_global=include_global).filter(tag=tag),
        request.query_params,
    )
    paginator = OctonomyLimitOffsetPagination()
    page = paginator.paginate_queryset(queryset, request)
    serializer = TagAliasSerializer(page, many=True)
    return paginator.get_paginated_response(serializer.data)


@extend_schema(
    parameters=[
        OpenApiParameter("slug", str, required=True),
        OpenApiParameter("application_id", str, required=False),
        OpenApiParameter("type", str, required=False),
        OpenApiParameter("scope", str, required=False),
    ],
    responses=TagResolutionSerializer,
)
@require_scopes(get="tags:read")
@api_view(["GET"])
def tag_resolution(request):
    tenant_id = require_tenant(request)
    slug = request.query_params.get("slug")
    if not slug:
        raise ValidationError({"slug": ["This query parameter is required."]})
    validate_slug_like(slug, "slug")
    application_id = maybe_validate_application_id(request)
    tag_type = request.query_params.get("type")
    if tag_type:
        validate_slug_like(tag_type, "type")

    scope_context = scope_context_for_request(request)
    result = resolve_tag_reference(
        tenant_id=tenant_id,
        slug=slug,
        application_id=application_id,
        tag_type=tag_type,
        scope_context=scope_context,
        scope_qualifier=request.query_params.get("scope"),
        # Fail-closed: global rows are reachable only when the caller is
        # authorized for global (include_global opt-in), same as list/detail.
        authorized_global=request_include_global(request),
    )
    apply_usage_counts([result["tag"]], scope_context, mode=usage_count_mode_for_request(request))
    return data_response(TagResolutionSerializer(result).data)
