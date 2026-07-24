from __future__ import annotations

from typing import Any

from django.http import Http404
from rest_framework import exceptions, status
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler


class DomainError(Exception):
    status_code = status.HTTP_400_BAD_REQUEST
    code = "validation_error"
    message = "Request validation failed."

    def __init__(self, message: str | None = None, details: Any | None = None) -> None:
        super().__init__(message or self.message)
        self.message = message or self.message
        self.details = details or {}


class ConflictError(DomainError):
    status_code = status.HTTP_409_CONFLICT
    code = "conflict"
    message = "The request conflicts with existing data."


class TenantMismatchError(DomainError):
    code = "tenant_mismatch"
    message = "Requested data does not belong to the current tenant."


class ApplicationMismatchError(DomainError):
    code = "application_mismatch"
    message = "Tag cannot be assigned in this application."


class InactiveTagError(DomainError):
    code = "inactive_tag"
    message = "Inactive tags cannot be assigned."


class NamespaceNotSupportedError(DomainError):
    # v1 is global-only. A namespaced client that misroutes to /api/v1 must fail
    # loudly here rather than silently reading or writing the global namespace.
    code = "namespace_not_supported"
    message = "Namespace headers are not supported on this API version."


class NamespaceHeaderError(DomainError):
    # Structurally invalid X-Namespace-* headers on a version that supports them
    # (reserved 'global', type without id, blank, or a folded/repeated header).
    code = "namespace_invalid"
    message = "Namespace headers are invalid."


class NamespacedWritesDisabledError(DomainError):
    # Kill-switch: persisting namespaced rows stays off until audit/outbox carry
    # namespace (S5) and rollout controls land (S7). Reads are unaffected.
    status_code = status.HTTP_403_FORBIDDEN
    code = "namespaced_writes_disabled"
    message = "Namespaced writes are not enabled."


class NamespaceApiDisabledError(DomainError):
    # Rollback edge gate (S7): NAMESPACE_V2_API_ENABLED is off, so the namespaced
    # v2 surface is withdrawn. Only namespaced v2 requests hit this — global v1/v2
    # traffic is unaffected, so the first rollback step stops merchant traffic
    # without stranding global clients. 503 (temporary, config-driven) not 404.
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "namespace_api_disabled"
    message = "The namespaced v2 API is not enabled on this deployment."


class AmbiguousResolutionError(DomainError):
    # NS-2: two or more equally-specific tags/aliases match a slug at the same
    # resolution scope rung, so resolution cannot deterministically pick one. This
    # is caller-resolvable — narrow with application_id, type, or a scope qualifier —
    # so it is a 400 (like the sibling "provide type" ambiguity), with a distinct
    # code and a details hint naming the axis that disambiguates.
    code = "ambiguous_resolution"
    message = "Multiple equally-specific matches; narrow application_id, type, or scope."


class ScopeImmutableError(ConflictError):
    # NS-1: a row's scope — application_id, namespace_type, namespace_id — is fixed
    # at creation. Changing it afterwards would orphan attached assignments, child
    # tags, aliases, and vocabulary references (legal in the old scope, illegal in
    # the new one) and could silently reassign merchant-private data, so scope moves
    # are rejected outright. Re-create the row in the target scope instead. 409 keeps
    # the status of the scope-move rejection this supersedes.
    code = "scope_immutable"
    message = "A row's application and namespace scope cannot be changed after creation."


def error_response(code: str, message: str, details: Any, request, http_status: int) -> Response:
    request_id = getattr(request, "request_id", None)
    # Stamp the error code onto the underlying HttpRequest so request-completion
    # logging can report the failure reason (4xx-by-reason and auth-deny metrics).
    # Every API error funnels through here, so this is the single place that sees
    # the resolved code for both DRF and Octonomy domain errors.
    if request is not None:
        underlying = getattr(request, "_request", request)
        underlying.error_code = code
    # Keep every public API error in one envelope so clients can reliably inspect
    # error.code, error.message, error.details, and error.request_id regardless of
    # whether the error came from DRF or Octonomy domain validation.
    return Response(
        {
            "error": {
                "code": code,
                "message": message,
                "details": details,
                "request_id": request_id,
            }
        },
        status=http_status,
    )


def exception_handler(exc, context):
    request = context.get("request")

    if isinstance(exc, DomainError):
        # DomainError subclasses carry product-specific error codes such as
        # tenant_mismatch and application_mismatch; preserve those instead of
        # flattening them into DRF generic validation responses.
        return error_response(exc.code, exc.message, exc.details, request, exc.status_code)

    if isinstance(exc, Http404):
        return error_response(
            "not_found", "Resource not found.", {}, request, status.HTTP_404_NOT_FOUND
        )

    response = drf_exception_handler(exc, context)
    if response is None:
        return None

    code = "validation_error"
    message = "Request validation failed."

    if isinstance(exc, exceptions.NotAuthenticated | exceptions.AuthenticationFailed):
        code = "authentication_required"
        message = "Authentication credentials were not provided."
    elif isinstance(exc, exceptions.PermissionDenied):
        code = "forbidden"
        message = "You do not have permission to perform this action."
    elif response.status_code == status.HTTP_404_NOT_FOUND:
        code = "not_found"
        message = "Resource not found."

    return error_response(code, message, response.data, request, response.status_code)
