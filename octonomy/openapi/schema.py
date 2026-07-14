"""OpenAPI customization hooks.

The v1 and v2 contracts are generated from one view tree (the version shim), so
the namespace request surface — the ``X-Namespace-*`` headers and the
``include_global`` opt-in — must be documented on **v2 only**. This
postprocessing hook injects those parameters when drf-spectacular generates the
v2 schema (``generator.api_version == "v2"``) and leaves the v1 schema untouched.
"""

from __future__ import annotations

from drf_spectacular.settings import spectacular_settings

NAMESPACE_TYPE_PARAMETER = {
    "name": "X-Namespace-Type",
    "in": "header",
    "required": False,
    "description": (
        "Namespace type for merchant/sub-tenant isolation. Omit for the global "
        "(tenant-shared) namespace. When present, X-Namespace-ID is required and "
        "the request must also carry an application_id. The literal 'global' is "
        "reserved. Values are opaque, caller-canonical strings and are not "
        "case-folded."
    ),
    "schema": {"type": "string"},
}

NAMESPACE_ID_PARAMETER = {
    "name": "X-Namespace-ID",
    "in": "header",
    "required": False,
    "description": "Namespace id. Required whenever X-Namespace-Type is present.",
    "schema": {"type": "string"},
}

APPLICATION_ID_PARAMETER = {
    "name": "application_id",
    "in": "query",
    "required": False,
    "description": (
        "Application scope. Required for namespaced requests (when X-Namespace-Type "
        "is present) because namespace isolation sits below application — a "
        "namespaced request without it is rejected. Optional for global requests."
    ),
    "schema": {"type": "string"},
}

INCLUDE_GLOBAL_PARAMETER = {
    "name": "include_global",
    "in": "query",
    "required": False,
    "description": (
        "Merchant (namespaced) reads exclude global rows by default. Set true to "
        "also return global rows the caller is authorized for (fail-closed: an "
        "exact merchant grant that lacks global authority still sees none)."
    ),
    "schema": {"type": "boolean", "default": False},
}

_SAFE_METHODS = {"get", "head"}
_NAMESPACE_RESPONSE_SCHEMAS = {
    "Assignment",
    "AuditLog",
    "ResourceTag",
    "Tag",
    "TagAlias",
    "TagResource",
    "Vocabulary",
}


def add_namespace_parameters(result, generator, request, public, **kwargs):
    # Both versions mirror the package version (versioning.md); the URL prefix
    # carries the contract major. Drop drf-spectacular's " (vN)" suffix so the
    # version-check release gate keeps matching the bare package version.
    if spectacular_settings.VERSION:
        result.setdefault("info", {})["version"] = spectacular_settings.VERSION

    api_version = getattr(generator, "api_version", None)
    if api_version != "v2":
        # Response serializers declare namespace identity so the v2 schema and
        # runtime payload can expose row ownership. Remove those properties from
        # the shared v1 components to preserve the established v1 contract.
        for name in _NAMESPACE_RESPONSE_SCHEMAS:
            schema = result.get("components", {}).get("schemas", {}).get(name)
            if schema is None:
                continue
            for field in ("namespace_type", "namespace_id"):
                schema.get("properties", {}).pop(field, None)
                if field in schema.get("required", []):
                    schema["required"].remove(field)
        return result

    # Only the versioned API paths implement the namespace contract. Unversioned
    # routes (e.g. /health/live, /health/ready) appear in every schema and must
    # not advertise X-Namespace-* / include_global.
    version_prefix = f"/api/{api_version}/"
    for path, path_item in result.get("paths", {}).items():
        if not path.startswith(version_prefix):
            continue
        for method, operation in path_item.items():
            if not isinstance(operation, dict) or "responses" not in operation:
                continue
            parameters = operation.setdefault("parameters", [])
            _add_parameter(parameters, NAMESPACE_TYPE_PARAMETER)
            _add_parameter(parameters, NAMESPACE_ID_PARAMETER)
            # A namespaced request must name its application; document the query
            # parameter on operations that don't already declare one, so generated
            # clients can construct valid namespaced calls (detail GET/DELETE, audit).
            _add_parameter(parameters, APPLICATION_ID_PARAMETER)
            if method.lower() in _SAFE_METHODS:
                _add_parameter(parameters, INCLUDE_GLOBAL_PARAMETER)

    return result


def _add_parameter(parameters, parameter) -> None:
    key = (parameter["name"], parameter["in"])
    if any((p.get("name"), p.get("in")) == key for p in parameters):
        return
    parameters.append(dict(parameter))
