from __future__ import annotations

import uuid

from django.db import models
from django.db.models import Q

from octonomy.core.models import NamespaceScopedModel, namespace_scope_constraint


class TagAssignmentQuerySet(models.QuerySet):
    def for_tenant(self, tenant_id: str):
        return self.filter(tenant_id=tenant_id)

    def for_resource(self, application_id: str, resource_type: str, resource_id: str):
        return self.filter(
            application_id=application_id,
            resource_type=resource_type,
            resource_id=resource_id,
        )


class TagAssignment(NamespaceScopedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant_id = models.CharField(max_length=100)
    application_id = models.CharField(max_length=100)
    tag = models.ForeignKey(
        "tags.Tag",
        related_name="assignments",
        on_delete=models.RESTRICT,
    )
    resource_type = models.CharField(max_length=100)
    resource_id = models.CharField(max_length=255)
    assigned_by = models.CharField(max_length=255, null=True, blank=True)
    assigned_at = models.DateTimeField(auto_now_add=True)

    objects = TagAssignmentQuerySet.as_manager()

    class Meta:
        db_table = "tag_assignments"
        ordering = ["-assigned_at", "id"]
        constraints = [
            namespace_scope_constraint(),
            models.CheckConstraint(
                condition=models.Q(resource_type__regex=r"^[a-z][a-z0-9_-]*$"),
                name="assignment_resource_type_slug",
            ),
            models.CheckConstraint(
                condition=~models.Q(resource_id=""),
                name="assignment_resource_id_not_blank",
            ),
            # Assignments are the only Octonomy data stored for external
            # resources. This uniqueness rule is what makes repeated assignment
            # requests idempotent for the same tenant/application/resource/tag.
            models.UniqueConstraint(
                fields=["tenant_id", "application_id", "resource_type", "resource_id", "tag"],
                condition=Q(namespace_type__isnull=True, namespace_id__isnull=True),
                name="uniq_global_assignment_tag",
            ),
            models.UniqueConstraint(
                fields=[
                    "tenant_id",
                    "application_id",
                    "namespace_type",
                    "namespace_id",
                    "resource_type",
                    "resource_id",
                    "tag",
                ],
                condition=Q(namespace_type__isnull=False, namespace_id__isnull=False),
                name="uniq_ns_assignment_tag",
            ),
        ]
        indexes = [
            models.Index(
                fields=["tenant_id", "application_id", "resource_type", "resource_id"],
                name="tag_assignm_tenant__5fd6df_idx",
            ),
            models.Index(fields=["tenant_id", "tag"], name="tag_assignm_tenant__cd908e_idx"),
            models.Index(
                fields=["tenant_id", "application_id", "tag"],
                name="tag_assignm_tenant__cd770b_idx",
            ),
            models.Index(
                fields=["tenant_id", "resource_type", "resource_id"],
                name="tag_assignm_tenant__32ebe4_idx",
            ),
            models.Index(
                fields=[
                    "tenant_id",
                    "application_id",
                    "namespace_type",
                    "namespace_id",
                    "resource_type",
                    "resource_id",
                ],
                name="assign_scope_resource_idx",
            ),
            models.Index(
                fields=["tenant_id", "tag", "namespace_type", "namespace_id"],
                name="assign_tenant_tag_ns_idx",
            ),
            models.Index(
                fields=["tenant_id", "-assigned_at"],
                name="tag_assignm_tenant__2d9d49_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.tenant_id}/{self.application_id}/{self.resource_type}/{self.resource_id}"
