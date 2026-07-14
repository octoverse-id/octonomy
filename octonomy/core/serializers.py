from __future__ import annotations

NAMESPACE_IDENTITY_FIELDS = ("namespace_type", "namespace_id")


class NamespaceIdentityResponseMixin:
    # Response serializers declare the fields for v2/OpenAPI, then remove them
    # from legacy runtime payloads when the request is not on the v2 surface.

    def to_representation(self, instance):
        representation = super().to_representation(instance)
        if self.context.get("api_version") != "v2":
            for field in NAMESPACE_IDENTITY_FIELDS:
                representation.pop(field, None)
        return representation


def response_serializer_context(request) -> dict[str, str | None]:
    """Build the version context shared by top-level and nested serializers."""

    return {"api_version": getattr(request, "version", None)}
