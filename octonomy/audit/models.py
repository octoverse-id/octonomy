from __future__ import annotations

import uuid

from django.db import models


class AuditLogQuerySet(models.QuerySet):
    def for_tenant(self, tenant_id: str):
        return self.filter(tenant_id=tenant_id)


class AuditLog(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant_id = models.CharField(max_length=100)
    application_id = models.CharField(max_length=100, null=True, blank=True)
    action = models.CharField(max_length=100)
    entity_type = models.CharField(max_length=100)
    entity_id = models.CharField(max_length=255)
    tag_id = models.UUIDField(null=True, blank=True)
    resource_type = models.CharField(max_length=100, null=True, blank=True)
    resource_id = models.CharField(max_length=255, null=True, blank=True)
    actor_id = models.CharField(max_length=255, null=True, blank=True)
    request_id = models.CharField(max_length=100, null=True, blank=True)
    operation_id = models.UUIDField(default=uuid.uuid4)
    changes = models.JSONField(default=dict)
    metadata = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = AuditLogQuerySet.as_manager()

    class Meta:
        db_table = "audit_logs"
        ordering = ["-created_at", "id"]
        indexes = [
            models.Index(fields=["tenant_id", "-created_at"], name="audit_tenant_created_idx"),
            models.Index(
                fields=["tenant_id", "action", "-created_at"],
                name="audit_action_created_idx",
            ),
            models.Index(
                fields=["tenant_id", "entity_type", "entity_id", "-created_at"],
                name="audit_entity_created_idx",
            ),
            models.Index(
                fields=["tenant_id", "tag_id", "-created_at"],
                name="audit_tag_created_idx",
            ),
            models.Index(
                fields=["tenant_id", "application_id", "-created_at"],
                name="audit_app_created_idx",
            ),
            models.Index(
                fields=["tenant_id", "resource_type", "resource_id", "-created_at"],
                name="audit_res_created_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.tenant_id}/{self.action}/{self.entity_type}/{self.entity_id}"
