from __future__ import annotations

from rest_framework import serializers

from octonomy.core.serializers import NamespaceIdentityResponseMixin
from octonomy.core.validators import validate_external_id, validate_slug_like
from octonomy.tags.models import Vocabulary
from octonomy.tags.services import validate_metadata


class VocabularySerializer(NamespaceIdentityResponseMixin, serializers.ModelSerializer):
    class Meta:
        model = Vocabulary
        fields = [
            "id",
            "tenant_id",
            "application_id",
            "namespace_type",
            "namespace_id",
            "name",
            "slug",
            "description",
            "metadata",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "tenant_id", "created_at", "updated_at"]


class VocabularyWriteSerializer(serializers.Serializer):
    application_id = serializers.CharField(max_length=100, required=False, allow_null=True)
    name = serializers.CharField(max_length=255)
    slug = serializers.CharField(max_length=255)
    description = serializers.CharField(required=False, allow_blank=True, allow_null=True)
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


class VocabularyPatchSerializer(VocabularyWriteSerializer):
    name = serializers.CharField(max_length=255, required=False)
    slug = serializers.CharField(max_length=255, required=False)
    metadata = serializers.JSONField(required=False)
    is_active = serializers.BooleanField(required=False)
