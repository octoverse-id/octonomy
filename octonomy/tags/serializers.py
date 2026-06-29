from __future__ import annotations

from rest_framework import serializers

from octonomy.core.auth import GLOBAL_SCOPE
from octonomy.core.selectors import apply_namespace_filter
from octonomy.core.validators import validate_external_id, validate_slug_like
from octonomy.tags.models import Tag, Vocabulary
from octonomy.tags.services import validate_metadata


class TagSerializer(serializers.ModelSerializer):
    parent_id = serializers.UUIDField(source="parent.id", read_only=True)
    vocabulary_id = serializers.UUIDField(source="vocabulary.id", read_only=True)
    usage_count = serializers.SerializerMethodField()

    class Meta:
        model = Tag
        fields = [
            "id",
            "tenant_id",
            "application_id",
            "name",
            "slug",
            "type",
            "description",
            "parent_id",
            "vocabulary_id",
            "metadata",
            "is_active",
            "usage_count",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "tenant_id", "created_at", "updated_at"]

    def get_usage_count(self, tag) -> int:
        usage_count = getattr(tag, "usage_count", None)
        if usage_count is not None:
            return usage_count
        return tag.assignments.count()


class TagWriteSerializer(serializers.Serializer):
    application_id = serializers.CharField(max_length=100, required=False, allow_null=True)
    name = serializers.CharField(max_length=255)
    slug = serializers.CharField(max_length=255)
    type = serializers.CharField(max_length=100)
    description = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    parent_id = serializers.UUIDField(required=False, allow_null=True)
    vocabulary_id = serializers.UUIDField(required=False, allow_null=True)
    metadata = serializers.JSONField(required=False, default=dict)
    is_active = serializers.BooleanField(required=False, default=True)

    def validate_application_id(self, value):
        if value is not None:
            validate_external_id(value, "application_id")
        return value

    def validate_slug(self, value):
        return validate_slug_like(value, "slug")

    def validate_type(self, value):
        return validate_slug_like(value, "type")

    def validate_metadata(self, value):
        return validate_metadata(value)

    def validate_parent_id(self, value):
        if value is None:
            return None
        tenant_id = self.context["tenant_id"]
        scope_context = self.context.get("scope_context", GLOBAL_SCOPE)
        try:
            return apply_namespace_filter(
                Tag.objects.for_tenant(tenant_id),
                scope_context,
                include_global=True,
            ).get(id=value)
        except Tag.DoesNotExist:
            raise serializers.ValidationError("Parent tag was not found.")

    def validate_vocabulary_id(self, value):
        if value is None:
            return None
        tenant_id = self.context["tenant_id"]
        scope_context = self.context.get("scope_context", GLOBAL_SCOPE)
        try:
            return apply_namespace_filter(
                Vocabulary.objects.for_tenant(tenant_id),
                scope_context,
                include_global=True,
            ).get(id=value)
        except Vocabulary.DoesNotExist:
            raise serializers.ValidationError("Vocabulary was not found.")

    def to_internal_value(self, data):
        value = super().to_internal_value(data)
        if "parent_id" in value:
            value["parent"] = value.pop("parent_id")
        if "vocabulary_id" in value:
            value["vocabulary"] = value.pop("vocabulary_id")
        return value


class TagPatchSerializer(TagWriteSerializer):
    name = serializers.CharField(max_length=255, required=False)
    slug = serializers.CharField(max_length=255, required=False)
    type = serializers.CharField(max_length=100, required=False)
    metadata = serializers.JSONField(required=False)
    is_active = serializers.BooleanField(required=False)
