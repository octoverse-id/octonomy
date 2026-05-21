from __future__ import annotations

from datetime import timedelta

from django.utils import timezone
from rest_framework import exceptions
from rest_framework.permissions import BasePermission

from octonomy.service_auth.models import ServiceClient
from octonomy.service_auth.services import hash_service_token, parse_service_token

LAST_USED_UPDATE_INTERVAL = timedelta(seconds=60)


def require_scopes(**method_scopes: str):
    """Attach service-token scopes to a function-based DRF view."""

    normalized = {method.upper(): scope for method, scope in method_scopes.items()}

    def decorator(view_func):
        if hasattr(view_func, "cls"):
            view_func.cls.required_scopes = normalized
        else:
            view_func.required_scopes = normalized
        return view_func

    return decorator


def required_scope_for_request(request, view) -> str:
    required_scopes = getattr(view, "required_scopes", {})
    scope = required_scopes.get(request.method)
    if scope:
        return scope
    raise exceptions.PermissionDenied("Endpoint does not declare a required service scope.")


def application_ids_from_request(request) -> set[str]:
    application_ids = set()

    query_application_id = request.query_params.get("application_id")
    if query_application_id:
        application_ids.add(query_application_id)

    data = request.data if hasattr(request, "data") else {}
    if isinstance(data, dict):
        body_application_id = data.get("application_id")
        if body_application_id:
            application_ids.add(body_application_id)

    return application_ids


def matching_grants(client: ServiceClient, tenant_id: str, scope: str):
    return [
        grant
        for grant in client.grants.all()
        if grant.tenant_id == tenant_id and grant.has_scope(scope)
    ]


def tenant_grants(client: ServiceClient, tenant_id: str):
    return [grant for grant in client.grants.all() if grant.tenant_id == tenant_id]


class BearerTokenPermission(BasePermission):
    """Service-to-service bearer-token auth with tenant/application grants."""

    message = "Bearer authentication is required."

    def has_permission(self, request, view) -> bool:
        from octonomy.core.errors import ApplicationMismatchError, TenantMismatchError

        if getattr(view, "allow_unauthenticated", False):
            return True

        authorization = request.headers.get("Authorization", "")
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise exceptions.AuthenticationFailed("Bearer authentication is required.")

        token = token.strip()
        client = self.authenticate_client(token)
        request.service_client = client

        tenant_id = getattr(request, "tenant_id", None)
        if not tenant_id:
            raise exceptions.ValidationError({"X-Tenant-ID": ["This header is required."]})

        scope = required_scope_for_request(request, view)
        grants = matching_grants(client, tenant_id, scope)
        if not grants:
            if tenant_grants(client, tenant_id):
                raise exceptions.PermissionDenied("Service client does not have required scope.")
            raise TenantMismatchError(
                "Service client is not granted access to this tenant.",
                {"X-Tenant-ID": ["Tenant is not granted for this service client."]},
            )

        tenant_wide = [grant for grant in grants if grant.application_id is None]
        application_ids = application_ids_from_request(request)
        if tenant_wide:
            self.mark_client_used(client)
            return True

        if not application_ids:
            raise ApplicationMismatchError(
                "Tenant-wide grant is required when application_id is omitted.",
                {"application_id": ["Tenant-wide grant is required."]},
            )

        granted_applications = {grant.application_id for grant in grants}
        if application_ids.issubset(granted_applications):
            self.mark_client_used(client)
            return True

        raise ApplicationMismatchError(
            "Service client is not granted access to this application.",
            {"application_id": ["Application is not granted for this service client."]},
        )

    def authenticate_client(self, token: str) -> ServiceClient:
        parsed = parse_service_token(token)
        if parsed is None:
            raise exceptions.AuthenticationFailed("Invalid bearer token.")

        raw_token, key_prefix = parsed
        try:
            client = ServiceClient.objects.prefetch_related("grants").get(
                key_prefix=key_prefix,
                hashed_key=hash_service_token(raw_token),
            )
        except ServiceClient.DoesNotExist:
            raise exceptions.AuthenticationFailed("Invalid bearer token.")

        if not client.is_active:
            raise exceptions.PermissionDenied("Service client is inactive.")

        if client.expires_at and client.expires_at <= timezone.now():
            raise exceptions.PermissionDenied("Service client token is expired.")

        return client

    def mark_client_used(self, client: ServiceClient) -> None:
        now = timezone.now()
        if client.last_used_at and now - client.last_used_at <= LAST_USED_UPDATE_INTERVAL:
            return
        client.last_used_at = now
        client.save(update_fields=["last_used_at"])
