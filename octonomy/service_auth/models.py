from __future__ import annotations

import uuid

from django.db import models
from django.db.models import Q

from octonomy.core.models import NamespaceScopedModel, namespace_scope_constraint


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


class ServiceClientGrant(NamespaceScopedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    service_client = models.ForeignKey(
        ServiceClient,
        related_name="grants",
        on_delete=models.CASCADE,
    )
    tenant_id = models.CharField(max_length=100)
    application_id = models.CharField(max_length=100, null=True, blank=True)
    namespace_wildcard = models.BooleanField(default=False)
    scopes = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "service_client_grants"
        constraints = [
            namespace_scope_constraint(),
            # A grant is either tenant-wide (application_id=NULL) or scoped to one
            # application. Namespace wildcard is an explicit opt-in boolean so it
            # cannot collide with caller-owned namespace_type strings.
            models.CheckConstraint(
                condition=Q(namespace_wildcard=False)
                | Q(namespace_type__isnull=True, namespace_id__isnull=True),
                name="svc_grant_wildcard_null_ns",
            ),
            models.UniqueConstraint(
                fields=["service_client", "tenant_id", "application_id"],
                condition=Q(
                    application_id__isnull=False,
                    namespace_type__isnull=True,
                    namespace_id__isnull=True,
                    namespace_wildcard=False,
                ),
                name="uniq_service_app_global_grant",
            ),
            models.UniqueConstraint(
                fields=["service_client", "tenant_id"],
                condition=Q(
                    application_id__isnull=True,
                    namespace_type__isnull=True,
                    namespace_id__isnull=True,
                    namespace_wildcard=False,
                ),
                name="uniq_service_tenant_global",
            ),
            models.UniqueConstraint(
                fields=[
                    "service_client",
                    "tenant_id",
                    "application_id",
                    "namespace_type",
                    "namespace_id",
                ],
                condition=Q(
                    application_id__isnull=False,
                    namespace_type__isnull=False,
                    namespace_id__isnull=False,
                    namespace_wildcard=False,
                ),
                name="uniq_service_app_ns_grant",
            ),
            models.UniqueConstraint(
                fields=["service_client", "tenant_id", "application_id"],
                condition=Q(application_id__isnull=False, namespace_wildcard=True),
                name="uniq_service_app_ns_wild",
            ),
            models.UniqueConstraint(
                fields=["service_client", "tenant_id"],
                condition=Q(application_id__isnull=True, namespace_wildcard=True),
                name="uniq_service_tenant_ns_wild",
            ),
        ]
        indexes = [
            models.Index(
                fields=["tenant_id", "application_id"],
                name="svc_grant_tenant_app_idx",
            ),
            models.Index(
                fields=["tenant_id", "application_id", "namespace_type", "namespace_id"],
                name="svc_grant_scope_idx",
            ),
            models.Index(
                fields=["tenant_id", "namespace_wildcard"],
                name="svc_grant_wildcard_idx",
            ),
        ]

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes
