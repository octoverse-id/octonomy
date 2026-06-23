from __future__ import annotations

import uuid

from django.db import models
from django.utils import timezone

from octonomy.core.models import NamespaceScopedModel, namespace_scope_constraint


class OutboxEventQuerySet(models.QuerySet):
    def for_tenant(self, tenant_id: str):
        return self.filter(tenant_id=tenant_id)


class OutboxEvent(NamespaceScopedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PROCESSING = "processing", "Processing"
        PUBLISHED = "published", "Published"
        FAILED = "failed", "Failed"
        DEAD_LETTER = "dead_letter", "Dead letter"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant_id = models.CharField(max_length=100)
    application_id = models.CharField(max_length=100, null=True, blank=True)
    event_type = models.CharField(max_length=100)
    aggregate_type = models.CharField(max_length=100)
    aggregate_id = models.CharField(max_length=255)
    payload = models.JSONField(default=dict)
    metadata = models.JSONField(default=dict)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )
    attempts = models.PositiveIntegerField(
        default=0,
        help_text="Total dispatch attempts, including the one that published it.",
    )
    recoveries = models.PositiveIntegerField(
        default=0,
        help_text="Expired claim recoveries that did not reach the delivery transport.",
    )
    last_error = models.TextField(blank=True, default="")
    available_at = models.DateTimeField(default=timezone.now)
    claim_id = models.UUIDField(null=True, blank=True)
    claimed_at = models.DateTimeField(null=True, blank=True)
    claim_expires_at = models.DateTimeField(null=True, blank=True)
    published_at = models.DateTimeField(null=True, blank=True)
    operation_id = models.UUIDField(null=True, blank=True)
    request_id = models.CharField(max_length=100, null=True, blank=True)
    actor_id = models.CharField(max_length=255, null=True, blank=True)
    tag_id = models.UUIDField(null=True, blank=True)
    resource_type = models.CharField(max_length=100, null=True, blank=True)
    resource_id = models.CharField(max_length=255, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = OutboxEventQuerySet.as_manager()

    class Meta:
        db_table = "outbox_events"
        ordering = ["created_at", "id"]
        constraints = [
            namespace_scope_constraint(),
        ]
        indexes = [
            models.Index(
                fields=["status", "available_at", "created_at"],
                name="outbox_pending_idx",
            ),
            models.Index(
                fields=["status", "claim_expires_at"],
                name="outbox_claim_exp_idx",
            ),
            models.Index(
                fields=["tenant_id", "-created_at"],
                name="outbox_tenant_created_idx",
            ),
            models.Index(
                fields=[
                    "tenant_id",
                    "application_id",
                    "namespace_type",
                    "namespace_id",
                    "-created_at",
                ],
                name="outbox_scope_created_idx",
            ),
            models.Index(
                fields=["aggregate_type", "aggregate_id", "-created_at"],
                name="outbox_aggregate_idx",
            ),
            models.Index(
                fields=["event_type", "-created_at"],
                name="outbox_type_created_idx",
            ),
            models.Index(
                fields=["tenant_id", "resource_type", "resource_id", "-created_at"],
                name="outbox_resource_idx",
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
                name="outbox_scope_resource_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.status}/{self.event_type}/{self.aggregate_type}/{self.aggregate_id}"
