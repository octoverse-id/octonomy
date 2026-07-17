from __future__ import annotations

import logging

from django.http import HttpResponse
from django.test import RequestFactory

from octonomy.core.auth import ScopeContext
from octonomy.core.middleware import RequestContextMiddleware


def test_request_completed_log_includes_resolved_namespace(caplog):
    request = RequestFactory().get(
        "/api/v2/tags",
        headers={"X-Tenant-ID": "tenant_a"},
    )

    def get_response(inner_request):
        inner_request.scope_context = ScopeContext("merchant", "merchant_a")
        return HttpResponse(status=200)

    caplog.set_level(logging.INFO, logger="octonomy.requests")
    response = RequestContextMiddleware(get_response)(request)
    record = next(record for record in caplog.records if record.message == "request_completed")

    assert response["X-Request-ID"].startswith("req_")
    assert record.tenant_id == "tenant_a"
    assert record.namespace_type == "merchant"
    assert record.namespace_id == "merchant_a"


def test_request_completed_log_includes_version_and_error_code(caplog):
    request = RequestFactory().post(
        "/api/v2/tags",
        headers={"X-Tenant-ID": "tenant_a"},
    )

    def get_response(inner_request):
        inner_request.scope_context = ScopeContext("merchant", "merchant_a")
        inner_request.api_version = "v2"
        inner_request.error_code = "namespaced_writes_disabled"
        return HttpResponse(status=403)

    caplog.set_level(logging.INFO, logger="octonomy.requests")
    RequestContextMiddleware(get_response)(request)
    record = next(record for record in caplog.records if record.message == "request_completed")

    assert record.version == "v2"
    assert record.error_code == "namespaced_writes_disabled"
    assert record.status_code == 403


def test_request_completed_log_uses_null_namespace_without_resolved_context(caplog):
    request = RequestFactory().get(
        "/health/live",
        headers={"X-Tenant-ID": "tenant_a"},
    )

    caplog.set_level(logging.INFO, logger="octonomy.requests")
    RequestContextMiddleware(lambda _request: HttpResponse(status=200))(request)
    record = next(record for record in caplog.records if record.message == "request_completed")

    assert record.namespace_type is None
    assert record.namespace_id is None
