from __future__ import annotations

from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings
from django.urls import path
from django.utils import timezone
from rest_framework import exceptions
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework.test import APIClient, APIRequestFactory
from rest_framework.views import APIView

from octonomy.audit.models import AuditLog
from octonomy.core.auth import BearerTokenPermission, ScopeContext
from octonomy.service_auth.models import ServiceClient
from octonomy.service_auth.services import create_service_client_token, grant_allows
from tests.factories import make_tag

pytestmark = pytest.mark.django_db


@api_view(["GET"])
def unscoped_test_view(request):
    return Response({"ok": True})


urlpatterns = [path("unscoped", unscoped_test_view)]


class ScopedPermissionView:
    required_scopes = {"GET": "tags:read"}


def authenticated_client(token: str, tenant_id: str = "tenant_a") -> APIClient:
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}", HTTP_X_TENANT_ID=tenant_id)
    return client


def authenticated_client_with_scheme(
    token: str,
    *,
    scheme: str,
    tenant_id: str = "tenant_a",
) -> APIClient:
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"{scheme} {token}", HTTP_X_TENANT_ID=tenant_id)
    return client


def token_for(
    *,
    tenant_id: str = "tenant_a",
    application_id: str | None = None,
    scopes: list[str] | None = None,
    is_active: bool = True,
    expires_at=None,
) -> str:
    token, client = create_service_client_token(
        name=f"svc-{tenant_id}-{application_id or 'all'}",
        is_active=is_active,
        expires_at=expires_at,
        grants=[
            {
                "tenant_id": tenant_id,
                "application_id": application_id,
                "scopes": scopes or ["tags:read", "tags:write", "audit:read"],
            }
        ],
    )
    client.refresh_from_db()
    assert token not in client.hashed_key
    return token


def permission_request(
    token: str,
    *,
    application_id: str = "commerce",
    scope_context: ScopeContext,
    tenant_id: str = "tenant_a",
):
    raw_request = APIRequestFactory().get(
        "/api/v2/tags",
        {"application_id": application_id},
        HTTP_AUTHORIZATION=f"Bearer {token}",
        HTTP_X_TENANT_ID=tenant_id,
    )
    request = APIView().initialize_request(raw_request)
    request.tenant_id = tenant_id
    request.scope_context = scope_context
    request.requested_scope_contexts = (scope_context,)
    return request


def test_valid_service_token_can_access_granted_tenant():
    token = token_for()
    response = authenticated_client(token).get("/api/v1/tags")

    assert response.status_code == 200


def test_bearer_scheme_is_case_insensitive():
    token = token_for()
    response = authenticated_client_with_scheme(token, scheme="bearer").get("/api/v1/tags")

    assert response.status_code == 200


@pytest.mark.parametrize(
    "authorization",
    ["", "Token nope", "Bearer unknown", "Bearer octo_bad_token"],
)
def test_missing_malformed_and_unknown_tokens_are_rejected(authorization):
    client = APIClient()
    headers = {"HTTP_X_TENANT_ID": "tenant_a"}
    if authorization:
        headers["HTTP_AUTHORIZATION"] = authorization
    client.credentials(**headers)

    response = client.get("/api/v1/tags")

    assert response.status_code in {401, 403}
    assert response.json()["error"]["code"] == "authentication_required"


def test_overlong_tokens_are_rejected_before_lookup():
    client = APIClient()
    client.credentials(
        HTTP_AUTHORIZATION=f"Bearer octo_deadbeef_{'x' * 500}",
        HTTP_X_TENANT_ID="tenant_a",
    )

    response = client.get("/api/v1/tags")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "authentication_required"


def test_inactive_and_expired_tokens_are_rejected():
    inactive_token = token_for(is_active=False)
    expired_token = token_for(expires_at=timezone.now() - timedelta(minutes=1))

    inactive_response = authenticated_client(inactive_token).get("/api/v1/tags")
    expired_response = authenticated_client(expired_token).get("/api/v1/tags")

    assert inactive_response.status_code == 403
    assert expired_response.status_code == 403


def test_service_token_cannot_access_ungranted_tenant():
    token = token_for(tenant_id="tenant_a")
    response = authenticated_client(token, tenant_id="tenant_b").get("/api/v1/tags")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "tenant_mismatch"


def test_valid_token_without_tenant_header_is_rejected():
    token = token_for()
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

    response = client.get("/api/v1/tags")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"


def test_valid_token_without_tenant_header_is_rejected_on_audit_endpoint():
    token = token_for(scopes=["audit:read"])
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

    response = client.get("/api/v1/audit-logs")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"


@override_settings(ROOT_URLCONF=__name__)
def test_authenticated_view_without_declared_scope_is_rejected():
    token = token_for(scopes=["tags:read", "tags:write", "audit:read"])
    response = authenticated_client(token).get("/unscoped")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


def test_application_grant_blocks_other_application_access():
    token = token_for(application_id="commerce")

    allowed = authenticated_client(token).get("/api/v1/tags?application_id=commerce")
    denied = authenticated_client(token).get("/api/v1/tags?application_id=cms")

    assert allowed.status_code == 200
    assert denied.status_code == 400
    assert denied.json()["error"]["code"] == "application_mismatch"


def test_partial_multi_application_request_is_application_mismatch():
    token = token_for(application_id="commerce", scopes=["tags:write"])

    response = authenticated_client(token).post(
        "/api/v1/tags?application_id=commerce",
        {
            "name": "Featured",
            "slug": "featured",
            "type": "label",
            "application_id": "cms",
            "metadata": {},
        },
        format="json",
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "application_mismatch"


def test_tenant_wide_grant_can_access_multiple_applications():
    token = token_for(application_id=None)
    client = authenticated_client(token)

    commerce = client.get("/api/v1/tags?application_id=commerce")
    cms = client.get("/api/v1/tags?application_id=cms")

    assert commerce.status_code == 200
    assert cms.status_code == 200


def test_exact_namespace_grant_does_not_authorize_legacy_global_application_request():
    token, service_client = create_service_client_token(
        name="svc-namespace-only",
        grants=[
            {
                "tenant_id": "tenant_a",
                "application_id": "commerce",
                "scopes": ["tags:read"],
                "namespace_type": "merchant",
                "namespace_id": "merchant_a",
            }
        ],
    )

    response = authenticated_client(token).get("/api/v1/tags?application_id=commerce")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"
    assert (
        grant_allows(
            service_client,
            tenant_id="tenant_a",
            application_id="commerce",
            scope="tags:read",
        )
        is False
    )


def test_explicit_namespace_wildcard_authorizes_legacy_global_application_request():
    token, service_client = create_service_client_token(
        name="svc-namespace-admin",
        grants=[
            {
                "tenant_id": "tenant_a",
                "application_id": "commerce",
                "namespace_wildcard": True,
                "scopes": ["tags:read"],
            }
        ],
    )

    response = authenticated_client(token).get("/api/v1/tags?application_id=commerce")

    assert response.status_code == 200
    assert grant_allows(
        service_client,
        tenant_id="tenant_a",
        application_id="commerce",
        scope="tags:read",
    )


@pytest.mark.parametrize(
    ("grant_fields", "name_suffix"),
    [
        (
            {
                "namespace_type": "merchant",
                "namespace_id": "merchant_b",
            },
            "merchant-b",
        ),
        ({}, "global"),
    ],
    ids=["other-merchant-grant", "global-only-grant"],
)
def test_permission_denies_merchant_context_without_matching_namespace(grant_fields, name_suffix):
    token, _client = create_service_client_token(
        name=f"svc-merchant-denied-{name_suffix}",
        grants=[
            {
                "tenant_id": "tenant_a",
                "application_id": "commerce",
                "scopes": ["tags:read"],
                **grant_fields,
            }
        ],
    )
    scope_context = ScopeContext("merchant", "merchant_a")
    request = permission_request(token, scope_context=scope_context)

    with pytest.raises(exceptions.PermissionDenied) as exc_info:
        BearerTokenPermission().has_permission(request, ScopedPermissionView())

    assert "namespace" in str(exc_info.value).lower()
    assert request.authorized_scope_contexts == frozenset()


def test_read_only_scope_cannot_mutate_tags():
    token = token_for(scopes=["tags:read"])
    response = authenticated_client(token).post(
        "/api/v1/tags",
        {"name": "Featured", "slug": "featured", "type": "label", "metadata": {}},
        format="json",
    )

    assert response.status_code == 403


def test_write_scope_can_create_tag():
    token = token_for(scopes=["tags:read", "tags:write"])
    response = authenticated_client(token).post(
        "/api/v1/tags",
        {"name": "Featured", "slug": "featured", "type": "label", "metadata": {}},
        format="json",
    )

    assert response.status_code == 201


def test_audit_read_scope_is_required_for_audit_endpoints():
    make_tag(slug="featured")
    token_without_audit = token_for(scopes=["tags:read", "tags:write"])
    token_with_audit = token_for(scopes=["audit:read"])

    denied = authenticated_client(token_without_audit).get("/api/v1/audit-logs")
    allowed = authenticated_client(token_with_audit).get("/api/v1/audit-logs")

    assert denied.status_code == 403
    assert allowed.status_code == 200


def test_audit_actor_falls_back_to_service_client_identity():
    token, service_client = create_service_client_token(
        name="svc-audit-actor",
        grants=[
            {
                "tenant_id": "tenant_a",
                "application_id": None,
                "scopes": ["tags:read", "tags:write", "audit:read"],
            }
        ],
    )

    response = authenticated_client(token).post(
        "/api/v1/tags",
        {"name": "Featured", "slug": "featured", "type": "label", "metadata": {}},
        format="json",
    )

    assert response.status_code == 201
    assert AuditLog.objects.get(action="tag.created").actor_id == service_client.name


def test_last_used_at_updates_only_after_authorized_request_and_is_throttled():
    token, service_client = create_service_client_token(
        name="svc-last-used",
        grants=[
            {
                "tenant_id": "tenant_a",
                "application_id": None,
                "scopes": ["tags:read"],
            }
        ],
    )
    service_client.refresh_from_db()
    original_updated_at = service_client.updated_at

    denied = authenticated_client(token, tenant_id="tenant_b").get("/api/v1/tags")
    service_client.refresh_from_db()

    assert denied.status_code == 400
    assert service_client.last_used_at is None
    assert service_client.updated_at == original_updated_at

    allowed = authenticated_client(token).get("/api/v1/tags")
    service_client.refresh_from_db()
    first_last_used_at = service_client.last_used_at

    second_allowed = authenticated_client(token).get("/api/v1/tags")
    service_client.refresh_from_db()

    assert allowed.status_code == 200
    assert second_allowed.status_code == 200
    assert first_last_used_at is not None
    assert service_client.last_used_at == first_last_used_at
    assert service_client.updated_at == original_updated_at


def test_management_command_creates_token_and_revoke_command_deactivates_it():
    out = StringIO()
    call_command(
        "create_service_token",
        "--name",
        "svc-command",
        "--tenant",
        "tenant_a",
        "--application",
        "commerce",
        "--scope",
        "tags:read",
        "--scope",
        "tags:write",
        "--metadata",
        '{"owner":"platform"}',
        stdout=out,
    )
    output = out.getvalue()
    token = next(
        line.removeprefix("Token: ").strip()
        for line in output.splitlines()
        if line.startswith("Token: ")
    )
    client = ServiceClient.objects.get(name="svc-command")

    assert token.startswith(f"octo_{client.key_prefix}_")
    assert token not in client.hashed_key
    assert client.metadata == {"owner": "platform"}
    assert (
        authenticated_client(token).get("/api/v1/tags?application_id=commerce").status_code == 200
    )

    call_command("revoke_service_token", "--prefix", client.key_prefix)
    client.refresh_from_db()

    assert client.is_active is False
    assert (
        authenticated_client(token).get("/api/v1/tags?application_id=commerce").status_code == 403
    )


def test_management_command_creates_exact_namespace_grant():
    out = StringIO()
    call_command(
        "create_service_token",
        "--name",
        "svc-merchant-a",
        "--tenant",
        "tenant_a",
        "--application",
        "commerce",
        "--namespace-type",
        "merchant",
        "--namespace-id",
        "merchant_a",
        "--scope",
        "tags:read",
        stdout=out,
    )

    grant = ServiceClient.objects.get(name="svc-merchant-a").grants.get()

    assert grant.application_id == "commerce"
    assert grant.namespace_type == "merchant"
    assert grant.namespace_id == "merchant_a"
    assert grant.namespace_wildcard is False


def test_management_command_creates_explicit_namespace_wildcard_grant():
    call_command(
        "create_service_token",
        "--name",
        "svc-namespace-admin",
        "--tenant",
        "tenant_a",
        "--namespace-wildcard",
        "--scope",
        "tags:read",
        stdout=StringIO(),
    )

    grant = ServiceClient.objects.get(name="svc-namespace-admin").grants.get()

    assert grant.application_id is None
    assert grant.namespace_type is None
    assert grant.namespace_id is None
    assert grant.namespace_wildcard is True


@pytest.mark.parametrize(
    "args",
    [
        ["--namespace-type", "merchant"],
        ["--namespace-id", "merchant_a"],
        ["--namespace-type", "merchant", "--namespace-id", "merchant_a"],
        [
            "--namespace-type",
            "merchant",
            "--namespace-id",
            "merchant_a",
            "--namespace-wildcard",
        ],
        [
            "--application",
            "commerce",
            "--namespace-type",
            "global",
            "--namespace-id",
            "merchant_a",
        ],
    ],
)
def test_management_command_rejects_invalid_namespace_grant_arguments(args):
    with pytest.raises(CommandError):
        call_command(
            "create_service_token",
            "--name",
            "svc-invalid-namespace",
            "--tenant",
            "tenant_a",
            "--scope",
            "tags:read",
            *args,
            stdout=StringIO(),
        )


def test_seed_demo_prints_usable_demo_service_token():
    out = StringIO()
    call_command("seed_demo", stdout=out)
    output = out.getvalue()
    token = next(
        line.removeprefix("  token: ").strip()
        for line in output.splitlines()
        if line.startswith("  token: ")
    )

    response = authenticated_client(token, tenant_id="tenant_demo").get("/api/v1/tags")

    assert "Created demo service token." in output
    assert response.status_code == 200
