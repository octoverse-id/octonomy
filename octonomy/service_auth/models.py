from __future__ import annotations

import uuid

from django.db import models
from django.db.models import Q


class ServiceClient(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    key_prefix = models.CharField(max_length=32, unique=True)
    hashed_key = models.CharField(max_length=128, unique=True)
    is_active = models.BooleanField(default=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(default=dict)
    last_used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "service_clients"
        indexes = [
            models.Index(fields=["key_prefix"], name="svc_client_prefix_idx"),
            models.Index(fields=["is_active", "expires_at"], name="svc_client_active_idx"),
        ]

    def __str__(self) -> str:
        return self.name


class ServiceClientGrant(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    service_client = models.ForeignKey(
        ServiceClient,
        related_name="grants",
        on_delete=models.CASCADE,
    )
    tenant_id = models.CharField(max_length=100)
    application_id = models.CharField(max_length=100, null=True, blank=True)
    scopes = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "service_client_grants"
        constraints = [
            models.UniqueConstraint(
                fields=["service_client", "tenant_id", "application_id"],
                condition=Q(application_id__isnull=False),
                name="uniq_service_app_grant",
            ),
            models.UniqueConstraint(
                fields=["service_client", "tenant_id"],
                condition=Q(application_id__isnull=True),
                name="uniq_service_tenant_grant",
            )
        ]
        indexes = [
            models.Index(
                fields=["tenant_id", "application_id"],
                name="svc_grant_tenant_app_idx",
            ),
        ]

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes
