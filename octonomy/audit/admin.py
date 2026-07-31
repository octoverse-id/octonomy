"""Read-only diagnostics admin for AuditLog.

``audit_logs`` is an append-only table that grows unbounded, so the admin is view /
list / search / filter only (no add/change/delete), and its filters/search are limited
to indexed columns — a free-text scan over ``actor_id``/``request_id`` or the JSON
``changes`` would full-scan and time out at scale.
"""

from __future__ import annotations

from django.contrib import admin

from octonomy.audit.models import AuditLog
from octonomy.core.admin_base import ExactSearchModelAdmin, pretty_json


@admin.register(AuditLog)
class AuditLogAdmin(ExactSearchModelAdmin):
    list_display = (
        "created_at",
        "tenant_id",
        "action",
        "entity_type",
        "entity_id",
        "actor_id",
        "application_id",
        "scope_label",
    )
    # Filters aligned with audit_action_created_idx and the created_at ordering; no
    # free-text search on actor_id/request_id/JSON. entity_id search is a case-sensitive
    # __exact lookup (ExactSearchModelAdmin) rather than an ILIKE/iexact scan.
    list_filter = ("action", "entity_type", "created_at")
    search_fields = ("entity_id",)
    exact_search_field = "entity_id"
    date_hierarchy = "created_at"
    ordering = ("-created_at", "id")

    fields = (
        "created_at",
        "tenant_id",
        "application_id",
        "namespace_type",
        "namespace_id",
        "action",
        "entity_type",
        "entity_id",
        "tag_id",
        "resource_type",
        "resource_id",
        "actor_id",
        "request_id",
        "operation_id",
        "changes_pretty",
        "metadata_pretty",
    )
    readonly_fields = fields

    @admin.display(description="Scope")
    def scope_label(self, obj) -> str:
        if obj.namespace_type:
            return f"{obj.namespace_type}:{obj.namespace_id}"
        return "global"

    @admin.display(description="Changes")
    def changes_pretty(self, obj) -> str:
        return pretty_json(obj.changes)

    @admin.display(description="Metadata")
    def metadata_pretty(self, obj) -> str:
        return pretty_json(obj.metadata)
