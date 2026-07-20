"""Version resolution and the X-Namespace-* header contract (issue #42)."""

from __future__ import annotations

import pytest
from rest_framework import serializers
from rest_framework.test import APIClient, APIRequestFactory
from rest_framework.views import APIView

from octonomy.core.auth import GLOBAL_SCOPE, ScopeContext
from octonomy.core.errors import NamespaceHeaderError, NamespaceNotSupportedError
from octonomy.core.versioning import resolve_scope_context

pytestmark = pytest.mark.django_db


def make_request(path="/api/v2/tags", params=None, **headers):
    raw = APIRequestFactory().get(path, params or {}, **headers)
    return APIView().initialize_request(raw)


# --- resolver unit tests (fast, exhaustive on the header contract) ------------


def test_v2_without_headers_resolves_global():
    request = make_request()
    resolve_scope_context(request, "v2")
    assert request.scope_context == GLOBAL_SCOPE
    assert request.requested_scope_contexts == (GLOBAL_SCOPE,)


def test_v2_merchant_headers_resolve_scope():
    request = make_request(HTTP_X_NAMESPACE_TYPE="merchant", HTTP_X_NAMESPACE_ID="merchant_a")
    resolve_scope_context(request, "v2")
    assert request.scope_context == ScopeContext("merchant", "merchant_a")
    # Merchant reads exclude global by default: global is not in the requested set.
    assert request.requested_scope_contexts == (ScopeContext("merchant", "merchant_a"),)


def test_v2_include_global_adds_global_to_requested_set():
    request = make_request(
        params={"include_global": "true"},
        HTTP_X_NAMESPACE_TYPE="merchant",
        HTTP_X_NAMESPACE_ID="merchant_a",
    )
    resolve_scope_context(request, "v2")
    assert request.requested_scope_contexts == (
        ScopeContext("merchant", "merchant_a"),
        GLOBAL_SCOPE,
    )


def test_v2_include_global_is_ignored_for_a_global_request():
    request = make_request(params={"include_global": "true"})
    resolve_scope_context(request, "v2")
    assert request.requested_scope_contexts == (GLOBAL_SCOPE,)


def test_v2_type_without_id_is_rejected():
    request = make_request(HTTP_X_NAMESPACE_TYPE="merchant")
    with pytest.raises(NamespaceHeaderError):
        resolve_scope_context(request, "v2")


def test_v2_id_without_type_is_rejected():
    request = make_request(HTTP_X_NAMESPACE_ID="merchant_a")
    with pytest.raises(NamespaceHeaderError):
        resolve_scope_context(request, "v2")


def test_v1_namespace_reject_records_requested_namespace_for_logging():
    # A v1 request carrying namespace headers is rejected before scope resolution,
    # but the request must still carry the requested namespace so the reject stays
    # on the namespace dashboards.
    request = make_request(
        path="/api/v1/tags", HTTP_X_NAMESPACE_TYPE="merchant", HTTP_X_NAMESPACE_ID="merchant_a"
    )
    with pytest.raises(NamespaceNotSupportedError):
        resolve_scope_context(request, "v1")
    assert request._request.requested_namespace_type == "merchant"
    assert request._request.requested_namespace_id == "merchant_a"


def test_v2_malformed_reject_records_requested_namespace_for_logging():
    request = make_request(HTTP_X_NAMESPACE_TYPE="merchant")  # id missing -> rejected
    with pytest.raises(NamespaceHeaderError):
        resolve_scope_context(request, "v2")
    assert request._request.requested_namespace_type == "merchant"


def test_requested_namespace_is_truncated_for_logging():
    request = make_request(HTTP_X_NAMESPACE_TYPE="m" * 200, HTTP_X_NAMESPACE_ID="merchant_a")
    with pytest.raises(NamespaceHeaderError):  # overlong -> rejected
        resolve_scope_context(request, "v2")
    assert request._request.requested_namespace_type == "m" * 100  # capped at column width


def test_id_only_reject_still_flags_namespace_requested():
    # An id-only malformed pair leaves both the resolved and requested type null, so
    # namespace_requested is the classification that keeps it on the dashboard.
    request = make_request(HTTP_X_NAMESPACE_ID="merchant_a")
    with pytest.raises(NamespaceHeaderError):
        resolve_scope_context(request, "v2")
    assert request._request.requested_namespace_type is None
    assert request._request.namespace_requested is True


def test_global_request_does_not_flag_namespace_requested():
    request = make_request()
    resolve_scope_context(request, "v2")
    assert getattr(request._request, "namespace_requested", None) is None


def test_v2_reserved_global_type_is_rejected():
    request = make_request(HTTP_X_NAMESPACE_TYPE="global", HTTP_X_NAMESPACE_ID="merchant_a")
    with pytest.raises(NamespaceHeaderError):
        resolve_scope_context(request, "v2")


def test_v2_blank_type_is_rejected():
    request = make_request(HTTP_X_NAMESPACE_TYPE="   ", HTTP_X_NAMESPACE_ID="merchant_a")
    with pytest.raises(serializers.ValidationError):
        resolve_scope_context(request, "v2")


def test_v2_folded_header_is_rejected():
    request = make_request(
        HTTP_X_NAMESPACE_TYPE="merchant,merchant", HTTP_X_NAMESPACE_ID="merchant_a"
    )
    with pytest.raises(NamespaceHeaderError):
        resolve_scope_context(request, "v2")


def test_namespace_values_are_not_case_folded():
    request = make_request(HTTP_X_NAMESPACE_TYPE="Merchant", HTTP_X_NAMESPACE_ID="Merchant_A")
    resolve_scope_context(request, "v2")
    assert request.scope_context == ScopeContext("Merchant", "Merchant_A")


def test_v2_overlong_type_is_rejected():
    request = make_request(HTTP_X_NAMESPACE_TYPE="m" * 101, HTTP_X_NAMESPACE_ID="merchant_a")
    with pytest.raises(NamespaceHeaderError):
        resolve_scope_context(request, "v2")


def test_v2_overlong_id_is_rejected():
    request = make_request(HTTP_X_NAMESPACE_TYPE="merchant", HTTP_X_NAMESPACE_ID="m" * 101)
    with pytest.raises(NamespaceHeaderError):
        resolve_scope_context(request, "v2")


def test_v2_max_length_namespace_values_are_accepted():
    request = make_request(HTTP_X_NAMESPACE_TYPE="m" * 100, HTTP_X_NAMESPACE_ID="a" * 100)
    resolve_scope_context(request, "v2")
    assert request.scope_context == ScopeContext("m" * 100, "a" * 100)


def test_v1_without_headers_pins_global():
    request = make_request(path="/api/v1/tags")
    resolve_scope_context(request, "v1")
    assert request.scope_context == GLOBAL_SCOPE
    assert request.requested_scope_contexts == (GLOBAL_SCOPE,)


def test_v1_rejects_namespace_headers():
    request = make_request(path="/api/v1/tags", HTTP_X_NAMESPACE_TYPE="merchant")
    with pytest.raises(NamespaceNotSupportedError):
        resolve_scope_context(request, "v1")


# --- integration tests (through the routing + permission stack) ---------------


def test_v1_namespace_header_400_precedes_401_on_unauthenticated_request():
    # The v1 header rejection is resolved in determine_version, before auth, so a
    # misrouted namespaced client sees the precise 400 rather than a masking 401.
    client = APIClient()
    response = client.get(
        "/api/v1/tags",
        HTTP_X_TENANT_ID="tenant_a",
        HTTP_X_NAMESPACE_TYPE="merchant",
        HTTP_X_NAMESPACE_ID="merchant_a",
    )
    assert response.status_code == 400
    assert response.data["error"]["code"] == "namespace_not_supported"


def test_authenticated_v1_request_rejects_namespace_headers(api_client):
    response = api_client.get("/api/v1/tags", HTTP_X_NAMESPACE_TYPE="merchant")
    assert response.status_code == 400
    assert response.data["error"]["code"] == "namespace_not_supported"


def test_unknown_version_is_not_found(api_client):
    response = api_client.get("/api/v3/tags")
    assert response.status_code == 404


def test_v2_global_list_is_served(api_client):
    response = api_client.get("/api/v2/tags")
    assert response.status_code == 200


@pytest.mark.parametrize("path", ["/health/live", "/health/ready", "/api/schema/"])
def test_unversioned_routes_ignore_namespace_headers(path):
    # Unversioned routes get the default version from URLPathVersioning but are not
    # under /api/<version>/, so a probe reusing its v2 headers must not get a 400.
    client = APIClient()
    response = client.get(
        path,
        HTTP_X_NAMESPACE_TYPE="merchant",
        HTTP_X_NAMESPACE_ID="merchant_a",
    )
    assert response.status_code != 400
