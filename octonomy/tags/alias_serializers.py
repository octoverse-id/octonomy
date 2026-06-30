from __future__ import annotations

from rest_framework import serializers

from octonomy.core.auth import GLOBAL_SCOPE
from octonomy.core.selectors import apply_namespace_filter
from octonomy.core.validators import validate_external_id, validate_slug_like
from octonomy.tags.models import Tag, TagAlias
from octonomy.tags.serializers import TagSerializer
from octonomy.tags.services import validate_metadata


class TagAliasSerializer(serializers.ModelSerializer):
    tag_id = serializers.UUIDField(source="tag.id", read_only=True)

    class Meta:
        model = TagAlias
        fields = [
            "id",
            "tenant_id",
            "application_id",
            "tag_id",
            "name",
            "slug",
            "metadata",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "tenant_id", "created_at", "updated_at"]


class TagAliasWriteSerializer(serializers.Serializer):
    application_id = serializers.CharField(max_length=100, required=False, allow_null=True)
    tag_id = serializers.UUIDField()
    name = serializers.CharField(max_length=255)
    slug = serializers.CharField(max_length=255)
    metadata = serializers.JSONField(required=False, default=dict)
    is_active = serializers.BooleanField(required=False, default=True)

    def validate_application_id(self, value):
        if value is not None:
            validate_external_id(value, "application_id")
        return value

    def validate_slug(self, value):
        return validate_slug_like(value, "slug")

    def validate_metadata(self, value):
        return validate_metadata(value)

    def validate_tag_id(self, value):
        tenant_id = self.context["tenant_id"]
        scope_context = self.context.get("scope_context", GLOBAL_SCOPE)
        try:
            return apply_namespace_filter(
                Tag.objects.for_tenant(tenant_id),
                scope_context,
                include_global=True,
            ).get(id=value)
        except Tag.DoesNotExist:
            raise serializers.ValidationError("Tag was not found.")

    def to_internal_value(self, data):
        value = super().to_internal_value(data)
        if "tag_id" in value:
            value["tag"] = value.pop("tag_id")
        return value


class TagAliasPatchSerializer(TagAliasWriteSerializer):
    tag_id = serializers.UUIDField(required=False)
    name = serializers.CharField(max_length=255, required=False)
    slug = serializers.CharField(max_length=255, required=False)
    metadata = serializers.JSONField(required=False)
    is_active = serializers.BooleanField(required=False)


class TagResolutionSerializer(serializers.Serializer):
    matched_type = serializers.CharField()
    matched_alias = TagAliasSerializer(allow_null=True)
    tag = TagSerializer()
