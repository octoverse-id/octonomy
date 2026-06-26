from __future__ import annotations

import logging
import time
import uuid

logger = logging.getLogger("octonomy.requests")


class RequestContextMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.request_id = request.headers.get("X-Request-ID") or f"req_{uuid.uuid4().hex}"
        request.tenant_id = request.headers.get("X-Tenant-ID")

        started_at = time.monotonic()
        response = self.get_response(request)
        response["X-Request-ID"] = request.request_id

        scope_context = getattr(request, "scope_context", None)
        logger.info(
            "request_completed",
            extra={
                "request_id": request.request_id,
                "tenant_id": request.tenant_id,
                "namespace_type": getattr(scope_context, "namespace_type", None),
                "namespace_id": getattr(scope_context, "namespace_id", None),
                "method": request.method,
                "path": request.path,
                "status_code": response.status_code,
                "duration_ms": round((time.monotonic() - started_at) * 1000, 2),
            },
        )
        return response
