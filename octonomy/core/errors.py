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


def error_response(code: str, message: str, details: Any, request, http_status: int) -> Response:
    request_id = getattr(request, "request_id", None)
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

    if isinstance(exc, exceptions.NotAuthenticated):
        code = "authentication_required"
        message = "Authentication credentials were not provided."
    elif isinstance(exc, exceptions.PermissionDenied):
        code = "forbidden"
        message = "You do not have permission to perform this action."
    elif response.status_code == status.HTTP_404_NOT_FOUND:
        code = "not_found"
        message = "Resource not found."

    return error_response(code, message, response.data, request, response.status_code)
