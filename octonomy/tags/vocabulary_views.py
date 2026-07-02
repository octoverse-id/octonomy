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
    namespace_kwargs,
)
from octonomy.tags.vocabulary_selectors import filter_vocabularies, vocabularies_for_tenant
from octonomy.tags.vocabulary_serializers import (
    VocabularyPatchSerializer,
    VocabularySerializer,
    VocabularyWriteSerializer,
)
from octonomy.tags.vocabulary_services import (
    create_vocabulary,
    deactivate_vocabulary,
    update_vocabulary,
)


def require_tenant(request) -> str:
    if not request.tenant_id:
        raise ValidationError({"X-Tenant-ID": ["This header is required."]})
    return request.tenant_id


def scope_context_for_request(request):
    return getattr(request, "scope_context", GLOBAL_SCOPE)


def get_vocabulary_or_404(
    tenant_id: str,
    vocabulary_id,
    scope_context=GLOBAL_SCOPE,
    *,
    include_global: bool = True,
    application_ids=None,
    include_shared: bool = True,
):
    try:
        queryset = apply_application_filter(
            vocabularies_for_tenant(tenant_id, scope_context, include_global=include_global),
            application_ids,
            include_shared=include_shared,
        )
        return queryset.get(id=vocabulary_id)
    except Exception:
        raise NotFound("Vocabulary was not found.")


@extend_schema(
    methods=["GET"],
    parameters=[
        OpenApiParameter("application_id", str, required=False),
        OpenApiParameter("include_shared", bool, required=False),
        OpenApiParameter("slug", str, required=False),
        OpenApiParameter("is_active", bool, required=False),
        OpenApiParameter("q", str, required=False),
        OpenApiParameter("limit", int, required=False),
        OpenApiParameter("offset", int, required=False),
    ],
    responses=VocabularySerializer(many=True),
)
@extend_schema(
    methods=["POST"],
    request=VocabularyWriteSerializer,
    responses={201: VocabularySerializer},
)
@require_scopes(get="tags:read", post="tags:write")
@api_view(["GET", "POST"])
def vocabularies_collection(request):
    tenant_id = require_tenant(request)
    scope_context = scope_context_for_request(request)

    if request.method == "GET":
        queryset = filter_vocabularies(
            vocabularies_for_tenant(
                tenant_id, scope_context, include_global=request_include_global(request)
            ),
            request.query_params,
        )
        paginator = OctonomyLimitOffsetPagination()
        page = paginator.paginate_queryset(queryset, request)
        serializer = VocabularySerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)

    serializer = VocabularyWriteSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    vocabulary = create_vocabulary(
        tenant_id,
        {**serializer.validated_data, **namespace_kwargs(scope_context)},
        build_audit_context(request),
    )
    return data_response(VocabularySerializer(vocabulary).data, status=status.HTTP_201_CREATED)


@extend_schema(methods=["GET"], responses=VocabularySerializer)
@extend_schema(methods=["PATCH"], request=VocabularyPatchSerializer, responses=VocabularySerializer)
@extend_schema(methods=["DELETE"], responses={204: None})
@require_scopes(get="tags:read", patch="tags:write", delete="tags:write")
@api_view(["GET", "PATCH", "DELETE"])
def vocabulary_detail(request, vocabulary_id):
    tenant_id = require_tenant(request)
    # Writes (PATCH/DELETE) target the exact request scope; reads may fall back
    # to global rows only when the caller is authorized for the global namespace.
    include_global = request_include_global(request) if request.method == "GET" else False
    application_ids, include_shared = application_filter_params(request)
    vocabulary = get_vocabulary_or_404(
        tenant_id,
        vocabulary_id,
        scope_context_for_request(request),
        include_global=include_global,
        application_ids=application_ids,
        include_shared=include_shared,
    )

    if request.method == "GET":
        return data_response(VocabularySerializer(vocabulary).data)

    if request.method == "DELETE":
        deactivate_vocabulary(vocabulary, build_audit_context(request))
        return Response(status=status.HTTP_204_NO_CONTENT)

    serializer = VocabularyPatchSerializer(data=request.data, partial=True)
    serializer.is_valid(raise_exception=True)
    vocabulary = update_vocabulary(
        vocabulary,
        serializer.validated_data,
        build_audit_context(request),
    )
    return data_response(VocabularySerializer(vocabulary).data)
