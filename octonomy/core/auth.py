from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.utils import timezone
from rest_framework import exceptions
from rest_framework.permissions import BasePermission

from octonomy.core.models import RESERVED_NAMESPACE_TYPE_GLOBAL
from octonomy.service_auth.models import ServiceClient, ServiceClientGrant
from octonomy.service_auth.services import (
    hash_service_token,
    parse_service_token,
)

LAST_USED_UPDATE_INTERVAL = timedelta(seconds=60)

# HTTP methods that only read. Any other method persists state and, when it also
# carries a namespace scope, is gated by NAMESPACE_WRITE_ENABLED.
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


@dataclass(frozen=True)
class ScopeContext:
    """The namespace partition selected for one request."""

    namespace_type: str | None = None
    namespace_id: str | None = None

    def __post_init__(self) -> None:
        if (self.namespace_type is None) != (self.namespace_id is None):
            raise ValueError("namespace_type and namespace_id must both be set or both be null.")
        if self.namespace_type is None:
            return
        if not str(self.namespace_type).strip():
            raise ValueError("namespace_type cannot be blank.")
        if self.namespace_type == RESERVED_NAMESPACE_TYPE_GLOBAL:
            raise ValueError("The literal 'global' is reserved; omit namespace fields.")
        if not str(self.namespace_id).strip():
            raise ValueError("namespace_id cannot be blank.")

    @property
    def is_global(self) -> bool:
        return self.namespace_type is None


GLOBAL_SCOPE = ScopeContext()


def request_include_global(request) -> bool:
    """Whether a request may see global (tenant-shared) rows as a fallback.

    Global visibility is only granted when the request's authorized scope set
    actually includes the global namespace. A merchant request authenticated by
    an exact merchant grant deliberately excludes ``GLOBAL_SCOPE``, so it must
    not fall back to global rows for list/detail reads. The set is unset on
    legacy/global-only requests (and direct service calls), where defaulting to
    ``True`` preserves v1 behaviour.
    """

    authorized = getattr(request, "authorized_scope_contexts", None)
    if authorized is None:
        return True
    return GLOBAL_SCOPE in authorized


def request_authorizes_global_references(request) -> bool:
    """Whether write-time foreign keys may target global rows.

    Unlike ``request_include_global``, this is not a read opt-in. Write serializers
    need to know whether the token has an independent global grant before allowing
    a namespaced tag, alias, or assignment to reference a tenant-shared row. The
    permission class computes the decision across every application named by the
    request so an exact merchant grant cannot use reference resolution as a path
    around fail-closed global authorization.
    """

    authorized = getattr(request, "authorized_global_references", None)
    if authorized is None:
        # Preserve legacy/direct serializer behaviour outside authenticated API
        # requests. BearerTokenPermission always sets the value for public API
        # writes before the view or serializer runs.
        return True
    return authorized


def guard_namespace_write_enabled(scope_context: ScopeContext) -> None:
    """Reject persisting a namespaced row while the write kill-switch is off.

    Enforced at the domain-service layer so *every* write path is gated — HTTP
    requests, management commands, and any programmatic/background writer — not
    just HTTP routing (epic decision #11). Global writes (namespace-less) always
    pass; only a namespaced mutation reaches the flag check, so the error is a
    precise capability signal. Raw ORM writes (test factories, data migrations)
    are deliberately not gated: the kill-switch governs domain operations, not the
    storage layer.
    """

    if scope_context.is_global:
        return
    if getattr(settings, "NAMESPACE_WRITE_ENABLED", False):
        return

    from octonomy.core.errors import NamespacedWritesDisabledError

    raise NamespacedWritesDisabledError(
        "Namespaced writes are not enabled; this deployment accepts namespaced reads only.",
        {"namespace": ["Namespaced writes are disabled."]},
    )


def enforce_namespace_write_gate(request, scope_context: ScopeContext) -> None:
    """HTTP-layer kill-switch: refuse namespaced mutating requests early.

    Runs in ``BearerTokenPermission`` so an unauthorized namespaced write gets a
    clean 403 before the view. The authoritative gate is
    ``guard_namespace_write_enabled`` in the services (covers non-HTTP paths); this
    is the same check applied one layer earlier for a precise capability signal.
    """

    if request.method in SAFE_METHODS:
        return
    guard_namespace_write_enabled(scope_context)


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

    # Permission checks need every application id the request names, whether it
    # came from query parameters or the body. A service with app-scoped grants
    # must be authorized for all of them before the view runs.
    query_application_id = request.query_params.get("application_id")
    if query_application_id:
        application_ids.add(query_application_id)

    data = request.data if hasattr(request, "data") else {}
    if isinstance(data, dict):
        body_application_id = data.get("application_id")
        if body_application_id:
            application_ids.add(body_application_id)

    return application_ids


def grant_authorizes(
    grant: ServiceClientGrant,
    *,
    tenant_id: str,
    application_id: str | None,
    scope_context: ScopeContext,
    required_scope: str,
) -> bool:
    """Evaluate tenant, application, namespace, and API scope as one predicate."""

    if grant.tenant_id != tenant_id or not grant.has_scope(required_scope):
        return False

    if grant.application_id is not None and grant.application_id != application_id:
        return False

    # A namespaced request must identify its parent application even when the
    # grant itself is tenant-wide. Namespace isolation sits below application.
    if not scope_context.is_global and application_id is None:
        return False

    return grant_matches_namespace(grant, scope_context)


def grant_matches_namespace(grant: ServiceClientGrant, scope_context: ScopeContext) -> bool:
    """Whether a grant reaches the namespace partition, ignoring application."""

    if grant.namespace_wildcard:
        return True
    if scope_context.is_global:
        return grant.namespace_type is None and grant.namespace_id is None
    return (
        grant.namespace_type == scope_context.namespace_type
        and grant.namespace_id == scope_context.namespace_id
    )


def authorized_application_ids(request) -> set[str] | None:
    """Applications the caller may access in the request's namespace partition.

    Returns ``None`` when access is unrestricted (a tenant-wide grant reaches the
    namespace with no application bound), otherwise the set of application ids the
    caller is granted for. Object-by-id lookups bound the fetched row to this so a
    grant cannot reach a row in an application it is not authorized for — while an
    unrestricted caller can still fetch a row it is moving to another application
    (the request body names the destination, not the source).
    """

    client = getattr(request, "service_client", None)
    tenant_id = getattr(request, "tenant_id", None)
    if client is None or not tenant_id:
        return None

    scope_context = getattr(request, "scope_context", GLOBAL_SCOPE)
    required_scope = getattr(request, "required_scope", None)

    application_ids: set[str] = set()
    for grant in tenant_grants(client, tenant_id):
        if required_scope and not grant.has_scope(required_scope):
            continue
        if not grant_matches_namespace(grant, scope_context):
            continue
        if grant.application_id is None:
            return None
        application_ids.add(grant.application_id)
    return application_ids


def authorized_scope_contexts(
    grants: Iterable[ServiceClientGrant],
    *,
    tenant_id: str,
    application_id: str | None,
    requested_scopes: Iterable[ScopeContext],
    required_scope: str,
) -> frozenset[ScopeContext]:
    """Return only requested namespace partitions authorized by the grants."""

    grant_list = tuple(grants)
    return frozenset(
        scope_context
        for scope_context in requested_scopes
        if any(
            grant_authorizes(
                grant,
                tenant_id=tenant_id,
                application_id=application_id,
                scope_context=scope_context,
                required_scope=required_scope,
            )
            for grant in grant_list
        )
    )


def grant_application_matches(grant: ServiceClientGrant, application_id: str | None) -> bool:
    if grant.application_id is None:
        return True
    return grant.application_id == application_id


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
        # Stash the resolved scope so object-by-id lookups can bound the fetched
        # row to the applications this caller is granted for the same scope.
        request.required_scope = scope
        tenant_grant_list = tenant_grants(client, tenant_id)
        if not tenant_grant_list:
            raise TenantMismatchError(
                "Service client is not granted access to this tenant.",
                {"X-Tenant-ID": ["Tenant is not granted for this service client."]},
            )

        grants = [grant for grant in tenant_grant_list if grant.has_scope(scope)]
        if not grants:
            raise exceptions.PermissionDenied("Service client does not have required scope.")

        scope_context = getattr(request, "scope_context", GLOBAL_SCOPE)
        if not isinstance(scope_context, ScopeContext):
            raise exceptions.ValidationError(
                {"namespace": ["Request scope context was not resolved correctly."]}
            )
        request.scope_context = scope_context

        application_ids = application_ids_from_request(request)
        requested_scope_contexts = getattr(request, "requested_scope_contexts", (scope_context,))
        application_targets = application_ids or {None}

        authorized_by_application = {
            application_id: authorized_scope_contexts(
                grants,
                tenant_id=tenant_id,
                application_id=application_id,
                requested_scopes=requested_scope_contexts,
                required_scope=scope,
            )
            for application_id in application_targets
        }
        request.authorized_scope_contexts = frozenset.intersection(
            *authorized_by_application.values()
        )
        # Write-time references are allowed to fall back to global rows only
        # when every application target is covered by an independent global
        # grant. This is deliberately separate from include_global, which is a
        # read visibility opt-in rather than part of the write contract.
        request.authorized_global_references = all(
            any(
                grant_authorizes(
                    grant,
                    tenant_id=tenant_id,
                    application_id=application_id,
                    scope_context=GLOBAL_SCOPE,
                    required_scope=scope,
                )
                for grant in grants
            )
            for application_id in application_targets
        )

        if scope_context in request.authorized_scope_contexts:
            enforce_namespace_write_gate(request, scope_context)
            self.mark_client_used(client)
            return True

        if not scope_context.is_global:
            raise exceptions.PermissionDenied(
                "Service client is not granted access to this namespace."
            )

        if all(
            any(grant_application_matches(grant, application_id) for grant in grants)
            for application_id in application_targets
        ):
            # If every requested application is covered by some grant, the only
            # remaining failure for a global request is the grant's namespace
            # shape. Partial application coverage must stay application_mismatch
            # so clients get a precise error instead of a misleading 403.
            raise exceptions.PermissionDenied(
                "Service client is not granted access to this namespace."
            )

        if not application_ids:
            raise ApplicationMismatchError(
                "Tenant-wide grant is required when application_id is omitted.",
                {"application_id": ["Tenant-wide grant is required."]},
            )

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
            # Look up by prefix and peppered hash; the raw bearer token is never
            # stored, and the prefix alone is not sufficient to authenticate.
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
        # Throttle last-used writes so high-volume service traffic does not turn
        # every authorized request into a database update.
        if client.last_used_at and now - client.last_used_at <= LAST_USED_UPDATE_INTERVAL:
            return
        client.last_used_at = now
        client.save(update_fields=["last_used_at"])
