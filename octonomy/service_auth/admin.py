"""Read-only diagnostics admin for service tokens (ServiceClient / ServiceClientGrant).

Token creation and revocation stay in the existing management-command workflow; this
admin only lets a superuser inspect what exists. Two hard rules:

* ``ServiceClient.hashed_key`` is a secret and must never be exposed anywhere — list,
  detail, search, readonly, or form. Rather than *excluding* it (which would leak a
  future sensitive column added later), these admins use an explicit **safe-field
  allowlist**: only fields named here are ever rendered, so a new column stays hidden
  until someone deliberately adds it.
* Everything is view-only (no add/change/delete), inherited from ``ReadOnlyModelAdmin``.
"""

from __future__ import annotations

from django.contrib import admin

from octonomy.core.admin_base import ReadOnlyModelAdmin, pretty_json
from octonomy.service_auth.models import ServiceClient, ServiceClientGrant


@admin.register(ServiceClient)
class ServiceClientAdmin(ReadOnlyModelAdmin):
    # Safe-field allowlist — NEVER add hashed_key (or any future secret) here.
    list_display = (
        "name",
        "key_prefix",
        "is_active",
        "expires_at",
        "last_used_at",
        "created_at",
    )
    list_filter = ("is_active",)
    search_fields = ("name", "key_prefix")
    ordering = ("name", "id")

    fields = (
        "name",
        "key_prefix",
        "is_active",
        "expires_at",
        "last_used_at",
        "created_at",
        "updated_at",
        "metadata_pretty",
    )
    readonly_fields = fields

    @admin.display(description="Metadata")
    def metadata_pretty(self, obj) -> str:
        return pretty_json(obj.metadata)


@admin.register(ServiceClientGrant)
class ServiceClientGrantAdmin(ReadOnlyModelAdmin):
    list_display = (
        "service_client",
        "tenant_id",
        "application_id",
        "namespace_type",
        "namespace_id",
        "namespace_wildcard",
    )
    list_filter = ("namespace_wildcard",)
    search_fields = ("tenant_id", "application_id")
    ordering = ("tenant_id", "application_id", "id")

    fields = (
        "service_client",
        "tenant_id",
        "application_id",
        "namespace_type",
        "namespace_id",
        "namespace_wildcard",
        "scopes_pretty",
        "created_at",
        "updated_at",
    )
    readonly_fields = fields

    @admin.display(description="Scopes")
    def scopes_pretty(self, obj) -> str:
        return pretty_json(obj.scopes)
