"""Read-only diagnostics admin for OutboxEvent.

Append-only and unbounded like the audit log, so view/list/search/filter only. Filters
target indexed columns (``status``/``event_type``/``created_at``); the JSON payload and
metadata render read-only rather than being searchable.
"""

from __future__ import annotations

from django.contrib import admin

from octonomy.core.admin_base import ExactSearchModelAdmin, pretty_json
from octonomy.events.models import OutboxEvent


@admin.register(OutboxEvent)
class OutboxEventAdmin(ExactSearchModelAdmin):
    list_display = (
        "created_at",
        "status",
        "event_type",
        "aggregate_type",
        "aggregate_id",
        "tenant_id",
        "attempts",
        "available_at",
    )
    # Filters aligned with outbox_type_created_idx / the status indexes and created_at
    # ordering. aggregate_id search is a case-sensitive __exact lookup
    # (ExactSearchModelAdmin) rather than an ILIKE/iexact scan.
    list_filter = ("status", "event_type", "created_at")
    search_fields = ("aggregate_id",)
    exact_search_field = "aggregate_id"
    date_hierarchy = "created_at"
    ordering = ("-created_at", "id")

    fields = (
        "created_at",
        "updated_at",
        "status",
        "event_type",
        "aggregate_type",
        "aggregate_id",
        "tenant_id",
        "application_id",
        "namespace_type",
        "namespace_id",
        "tag_id",
        "resource_type",
        "resource_id",
        "attempts",
        "recoveries",
        "available_at",
        "published_at",
        "last_error",
        "actor_id",
        "request_id",
        "operation_id",
        "payload_pretty",
        "metadata_pretty",
    )
    readonly_fields = fields

    @admin.display(description="Payload")
    def payload_pretty(self, obj) -> str:
        return pretty_json(obj.payload)

    @admin.display(description="Metadata")
    def metadata_pretty(self, obj) -> str:
        return pretty_json(obj.metadata)
