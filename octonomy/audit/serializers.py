from __future__ import annotations

from rest_framework import serializers

from octonomy.audit.models import AuditLog


class AuditLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = AuditLog
        fields = [
            "id",
            "tenant_id",
            "application_id",
            "action",
            "entity_type",
            "entity_id",
            "tag_id",
            "resource_type",
            "resource_id",
            "actor_id",
            "request_id",
            "operation_id",
            "changes",
            "metadata",
            "created_at",
        ]
        read_only_fields = fields
