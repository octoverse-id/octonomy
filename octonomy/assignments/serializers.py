from __future__ import annotations

from rest_framework import serializers

from octonomy.assignments.models import TagAssignment
from octonomy.core.validators import validate_external_id, validate_slug_like
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
    tag_id = serializers.UUIDField()
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
        tenant_id = self.context["tenant_id"]
        try:
            return Tag.objects.for_tenant(tenant_id).get(id=value)
        except Tag.DoesNotExist:
            raise serializers.ValidationError("Tag was not found.")

    def to_internal_value(self, data):
        value = super().to_internal_value(data)
        if "tag_id" in value:
            value["tag"] = value.pop("tag_id")
        return value


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
    tag_ids = serializers.ListField(child=serializers.UUIDField(), allow_empty=False)
    assigned_by = serializers.CharField(
        max_length=255, required=False, allow_null=True, allow_blank=True
    )

    def validate_application_id(self, value):
        return validate_external_id(value, "application_id")

    def validate_resource_type(self, value):
        return validate_slug_like(value, "resource_type")

    def validate_resource_id(self, value):
        return validate_external_id(value, "resource_id")


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
    tag_ids = serializers.ListField(child=serializers.UUIDField(), allow_empty=True)


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
