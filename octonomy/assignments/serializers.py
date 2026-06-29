from __future__ import annotations

from django.conf import settings
from rest_framework import serializers

from octonomy.assignments.models import TagAssignment
from octonomy.core.auth import GLOBAL_SCOPE
from octonomy.core.selectors import apply_namespace_filter
from octonomy.core.validators import validate_external_id, validate_slug_like
from octonomy.tags.alias_selectors import active_aliases_for_resolution_bulk
from octonomy.tags.alias_services import resolve_assignable_alias
from octonomy.tags.models import Tag
from octonomy.tags.serializers import TagSerializer


class AssignmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = TagAssignment
        fields = [
            "id",
            "tenant_id",
            "application_id",
            "tag_id",
            "resource_type",
            "resource_id",
            "assigned_by",
            "assigned_at",
        ]
        read_only_fields = ["id", "tenant_id", "assigned_at"]


class AssignmentWriteSerializer(serializers.Serializer):
    application_id = serializers.CharField(max_length=100)
    tag_id = serializers.UUIDField(required=False)
    alias_id = serializers.UUIDField(required=False)
    alias_slug = serializers.CharField(max_length=255, required=False)
    resource_type = serializers.CharField(max_length=100)
    resource_id = serializers.CharField(max_length=255)
    assigned_by = serializers.CharField(
        max_length=255, required=False, allow_null=True, allow_blank=True
    )

    def validate_application_id(self, value):
        return validate_external_id(value, "application_id")

    def validate_resource_type(self, value):
        return validate_slug_like(value, "resource_type")

    def validate_resource_id(self, value):
        return validate_external_id(value, "resource_id")

    def validate_tag_id(self, value):
        return value

    def validate_alias_slug(self, value):
        return validate_slug_like(value, "alias_slug")

    def validate(self, attrs):
        identifiers = [field for field in ("tag_id", "alias_id", "alias_slug") if attrs.get(field)]
        if len(identifiers) != 1:
            raise serializers.ValidationError(
                {"non_field_errors": ["Provide exactly one of tag_id, alias_id, or alias_slug."]}
            )

        tenant_id = self.context["tenant_id"]
        application_id = attrs["application_id"]
        scope_context = self.context.get("scope_context", GLOBAL_SCOPE)
        if attrs.get("tag_id"):
            try:
                attrs["tag"] = apply_namespace_filter(
                    Tag.objects.for_tenant(tenant_id),
                    scope_context,
                    include_global=True,
                ).get(id=attrs.pop("tag_id"))
            except Tag.DoesNotExist:
                raise serializers.ValidationError({"tag_id": ["Tag was not found."]})
        else:
            attrs["tag"] = resolve_assignable_alias(
                tenant_id=tenant_id,
                application_id=application_id,
                scope_context=scope_context,
                alias_id=attrs.pop("alias_id", None),
                alias_slug=attrs.pop("alias_slug", None),
            )
        return attrs

    def to_internal_value(self, data):
        return super().to_internal_value(data)


class AssignmentDeleteSerializer(serializers.Serializer):
    application_id = serializers.CharField(max_length=100)
    tag_id = serializers.UUIDField()
    resource_type = serializers.CharField(max_length=100)
    resource_id = serializers.CharField(max_length=255)

    def validate_application_id(self, value):
        return validate_external_id(value, "application_id")

    def validate_resource_type(self, value):
        return validate_slug_like(value, "resource_type")

    def validate_resource_id(self, value):
        return validate_external_id(value, "resource_id")


class BulkAssignSerializer(serializers.Serializer):
    application_id = serializers.CharField(max_length=100)
    resource_type = serializers.CharField(max_length=100)
    resource_id = serializers.CharField(max_length=255)
    tag_ids = serializers.ListField(child=serializers.UUIDField(), required=False, allow_empty=True)
    alias_slugs = serializers.ListField(
        child=serializers.CharField(max_length=255), required=False, allow_empty=True
    )
    assigned_by = serializers.CharField(
        max_length=255, required=False, allow_null=True, allow_blank=True
    )

    def validate_application_id(self, value):
        return validate_external_id(value, "application_id")

    def validate_resource_type(self, value):
        return validate_slug_like(value, "resource_type")

    def validate_resource_id(self, value):
        return validate_external_id(value, "resource_id")

    def validate_alias_slugs(self, value):
        return [validate_slug_like(slug, "alias_slug") for slug in value]

    def validate(self, attrs):
        tag_ids = list(attrs.get("tag_ids", []))
        alias_slugs = list(attrs.pop("alias_slugs", []))
        if not tag_ids and not alias_slugs:
            raise serializers.ValidationError(
                {"non_field_errors": ["Provide tag_ids, alias_slugs, or both."]}
            )
        attrs["tag_ids"] = self.resolve_tag_ids(attrs, tag_ids, alias_slugs)
        return attrs

    def resolve_tag_ids(self, attrs, tag_ids: list, alias_slugs: list[str]) -> list:
        if not alias_slugs:
            return tag_ids

        tenant_id = self.context["tenant_id"]
        application_id = attrs["application_id"]
        scope_context = self.context.get("scope_context", GLOBAL_SCOPE)

        max_bulk = getattr(settings, "MAX_BULK_TAGS", 200)
        if len(tag_ids) + len(alias_slugs) > max_bulk:
            raise serializers.ValidationError(
                {"alias_slugs": [f"Maximum bulk size is {max_bulk}."]}
            )

        aliases = active_aliases_for_resolution_bulk(
            tenant_id,
            alias_slugs,
            application_id,
            scope_context,
            include_global=True,
        ).select_related("tag")
        resolved = {}
        for alias in aliases:
            if alias.slug not in resolved:
                resolved[alias.slug] = alias.tag

        missing = [slug for slug in alias_slugs if slug not in resolved]
        if missing:
            raise serializers.ValidationError(
                {"alias_slugs": [f"Aliases not found: {', '.join(missing)}"]}
            )

        for alias_slug in alias_slugs:
            tag = resolved[alias_slug]
            tag_ids.append(tag.id)
        return tag_ids


class BulkRemoveSerializer(serializers.Serializer):
    application_id = serializers.CharField(max_length=100)
    resource_type = serializers.CharField(max_length=100)
    resource_id = serializers.CharField(max_length=255)
    tag_ids = serializers.ListField(child=serializers.UUIDField(), allow_empty=False)

    def validate_application_id(self, value):
        return validate_external_id(value, "application_id")

    def validate_resource_type(self, value):
        return validate_slug_like(value, "resource_type")

    def validate_resource_id(self, value):
        return validate_external_id(value, "resource_id")


class ResourceReplaceSerializer(BulkAssignSerializer):
    tag_ids = serializers.ListField(child=serializers.UUIDField(), required=False, allow_empty=True)

    def validate(self, attrs):
        tag_ids = list(attrs.get("tag_ids", []))
        alias_slugs = list(attrs.pop("alias_slugs", []))
        attrs["tag_ids"] = self.resolve_tag_ids(attrs, tag_ids, alias_slugs)
        return attrs


class ResourceTagSerializer(serializers.Serializer):
    assignment_id = serializers.UUIDField(source="id")
    assigned_by = serializers.CharField(allow_null=True)
    assigned_at = serializers.DateTimeField()
    tag = TagSerializer()


class TagResourceSerializer(serializers.Serializer):
    application_id = serializers.CharField()
    resource_type = serializers.CharField()
    resource_id = serializers.CharField()
    assigned_by = serializers.CharField(allow_null=True)
    assigned_at = serializers.DateTimeField()
