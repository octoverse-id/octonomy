"""Version-aware request layer for the v1/v2 shim.

Both API versions are served by one view/serializer tree. This module resolves
the DRF version and, from it, the request's namespace ``ScopeContext``:

* **v1** is global-only. ``X-Namespace-*`` headers are rejected with a named 400
  (a misrouted namespaced client must fail loudly, never silently touch global).
* **v2** reads ``X-Namespace-*`` headers: absent type means global; a present
  type requires a present id. The literal ``global`` type is reserved.

Resolution runs in ``determine_version`` (called by ``APIView.initial`` *before*
authentication and permission checks), so ``BearerTokenPermission`` can read the
resolved context and the v1 header 400 is never masked by a 401/403.

Octonomy imports are deferred into the functions on purpose: DRF resolves
``DEFAULT_VERSIONING_CLASS`` while ``rest_framework.views`` is still initialising,
so importing ``octonomy.core.errors``/``auth`` at module load would form a cycle.
"""

from __future__ import annotations

from rest_framework.versioning import URLPathVersioning

NAMESPACE_TYPE_HEADER = "X-Namespace-Type"
NAMESPACE_ID_HEADER = "X-Namespace-ID"
INCLUDE_GLOBAL_PARAM = "include_global"


class NamespaceURLPathVersioning(URLPathVersioning):
    """URL-path versioning that also resolves the request namespace scope."""

    def determine_version(self, request, *args, **kwargs):
        version = super().determine_version(request, *args, **kwargs)
        # Resolve namespace scope only for requests actually served under the
        # versioned API prefix (/api/<version>/...). URLPathVersioning also hands the
        # default version to unversioned routes (health, schema, docs) and to
        # drf-spectacular's endpoint enumeration (whose request path still holds the
        # literal "{version}" placeholder); none of those should parse or reject
        # X-Namespace-* headers. path_info (not path) keeps this correct under a
        # script-name prefix.
        if request.path_info.startswith(f"/api/{version}/"):
            # Stamp the version onto the underlying HttpRequest *before* resolving
            # scope so request-completion logging reports it even when scope
            # resolution rejects the request (malformed headers, or the v2 edge gate
            # returning 503) — rollback traffic must stay on the version dashboard.
            _mirror_to_http_request(request, api_version=version)
            resolve_scope_context(request, version)
        return version


def resolve_scope_context(request, version: str) -> None:
    """Set ``scope_context`` and ``requested_scope_contexts`` on the request."""

    from octonomy.core.auth import GLOBAL_SCOPE
    from octonomy.core.errors import NamespaceNotSupportedError

    namespace_type = request.headers.get(NAMESPACE_TYPE_HEADER)
    namespace_id = request.headers.get(NAMESPACE_ID_HEADER)

    if version != "v2":
        if namespace_type is not None or namespace_id is not None:
            raise NamespaceNotSupportedError(
                "Namespace headers are not supported on /api/v1.",
                {"headers": [f"{NAMESPACE_TYPE_HEADER}/{NAMESPACE_ID_HEADER} require /api/v2."]},
            )
        _set(request, GLOBAL_SCOPE, include_global=False)
        return

    scope_context = _scope_from_headers(namespace_type, namespace_id)
    # Mirror the resolved scope before the edge gate so a request rejected with
    # 503 namespace_api_disabled still carries namespace_type/id into the request
    # log — otherwise rollback (V2_API off) traffic vanishes from the namespace
    # dashboards exactly when operators are watching them.
    _mirror_to_http_request(request, scope_context=scope_context)
    _reject_if_v2_api_disabled(scope_context)
    _set(request, scope_context, include_global=_wants_global(request))


def _reject_if_v2_api_disabled(scope_context) -> None:
    """Edge gate for the rollback ladder's first step.

    When ``NAMESPACE_V2_API_ENABLED`` is off, the namespaced v2 surface is
    withdrawn: a namespaced request is refused before authentication so no new
    merchant traffic is served. Global v2 requests (no namespace headers) are left
    untouched, so disabling the flag stops merchant traffic without breaking global
    clients — exactly what "disable V2_API first" needs on rollback.
    """

    from django.conf import settings

    if scope_context.is_global:
        return
    if getattr(settings, "NAMESPACE_V2_API_ENABLED", True):
        return

    from octonomy.core.errors import NamespaceApiDisabledError

    raise NamespaceApiDisabledError()


def usage_count_mode_for_request(request) -> str:
    """v2 reports namespace-visible usage counts; v1/global stays tenant-wide."""

    return "visible" if getattr(request, "version", None) == "v2" else "legacy"


def _scope_from_headers(namespace_type: str | None, namespace_id: str | None):
    from octonomy.core.auth import GLOBAL_SCOPE, ScopeContext
    from octonomy.core.errors import NamespaceHeaderError
    from octonomy.core.validators import validate_external_id

    if namespace_type is None and namespace_id is None:
        return GLOBAL_SCOPE

    if namespace_type is None:
        raise NamespaceHeaderError(
            f"{NAMESPACE_ID_HEADER} requires {NAMESPACE_TYPE_HEADER}.",
            {NAMESPACE_TYPE_HEADER: ["This header is required when a namespace id is sent."]},
        )
    if namespace_id is None:
        raise NamespaceHeaderError(
            f"{NAMESPACE_TYPE_HEADER} requires {NAMESPACE_ID_HEADER}.",
            {NAMESPACE_ID_HEADER: ["This header is required when a namespace type is sent."]},
        )

    # Format hygiene only (no allowlist/registry): reject blank/whitespace, a
    # comma that would indicate a folded/repeated header, and values wider than
    # the namespace column (so a namespaced write returns a structured 400 rather
    # than a DataError/500 when the row is persisted). Types/ids are opaque,
    # caller-canonical strings and are not case-folded.
    validate_external_id(namespace_type, NAMESPACE_TYPE_HEADER)
    validate_external_id(namespace_id, NAMESPACE_ID_HEADER)
    _reject_folded(namespace_type, NAMESPACE_TYPE_HEADER)
    _reject_folded(namespace_id, NAMESPACE_ID_HEADER)
    _reject_overlong(namespace_type, NAMESPACE_TYPE_HEADER)
    _reject_overlong(namespace_id, NAMESPACE_ID_HEADER)

    try:
        # ScopeContext.__post_init__ rejects the reserved 'global' type and blanks,
        # keeping the header contract and the persisted-row contract identical.
        return ScopeContext(namespace_type=namespace_type, namespace_id=namespace_id)
    except ValueError as exc:
        raise NamespaceHeaderError(str(exc)) from exc


def _reject_folded(value: str, header: str) -> None:
    from octonomy.core.errors import NamespaceHeaderError

    if "," in value:
        raise NamespaceHeaderError(
            f"{header} must be sent exactly once.",
            {header: ["Send this header exactly once; commas are not allowed."]},
        )


def _reject_overlong(value: str, header: str) -> None:
    from octonomy.core.errors import NamespaceHeaderError
    from octonomy.core.models import NAMESPACE_FIELD_MAX_LENGTH

    if len(value) > NAMESPACE_FIELD_MAX_LENGTH:
        raise NamespaceHeaderError(
            f"{header} must be at most {NAMESPACE_FIELD_MAX_LENGTH} characters.",
            {header: [f"Ensure this value has at most {NAMESPACE_FIELD_MAX_LENGTH} characters."]},
        )


def _wants_global(request) -> bool:
    params = getattr(request, "query_params", None)
    if params is None:
        return False
    if params.get(INCLUDE_GLOBAL_PARAM, "false").lower() == "true":
        return True
    # ``scope=global`` is an explicit global pin on tag resolution. Add global
    # to the requested authorization set for that endpoint only; treating the
    # parameter generically would let unrelated list endpoints use it as an
    # undocumented alias for include_global.
    resolver_match = getattr(request, "resolver_match", None)
    return (
        getattr(resolver_match, "url_name", None) == "tag-resolution"
        and params.get("scope") == "global"
    )


def _set(request, scope_context, *, include_global: bool) -> None:
    from octonomy.core.auth import GLOBAL_SCOPE

    request.scope_context = scope_context
    # Mirror the resolved scope onto the underlying HttpRequest so request-completion
    # logging (RequestContextMiddleware) sees the namespace: DRF's Request wrapper
    # proxies attribute reads to _request but not writes, so the middleware — which
    # holds the underlying request — would otherwise always log a null namespace.
    _mirror_to_http_request(request, scope_context=scope_context)
    # include_global is fail-closed: it only widens the *requested* set. Whether
    # global rows are actually visible still depends on the authorized scope set
    # computed in BearerTokenPermission via request_include_global().
    if include_global and not scope_context.is_global:
        request.requested_scope_contexts = (scope_context, GLOBAL_SCOPE)
    else:
        request.requested_scope_contexts = (scope_context,)


def _mirror_to_http_request(request, **attrs) -> None:
    """Copy attributes onto the underlying HttpRequest for the middleware.

    ``resolve_scope_context`` runs against DRF's ``Request`` wrapper, whose writes
    do not reach ``_request``; the request-logging middleware operates on that
    underlying ``HttpRequest``. Stashing there bridges the two without the
    middleware having to know about DRF's request object.
    """

    underlying = getattr(request, "_request", None)
    if underlying is None:
        return
    for name, value in attrs.items():
        setattr(underlying, name, value)
