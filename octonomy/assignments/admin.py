"""Service-backed admin for TagAssignment.

Assignments are the one taxonomy model that is not soft-deleted: they are created
idempotently via ``assign_tag`` and removed via ``remove_tag_assignment`` (a
service-backed hard delete that still emits audit + outbox events), and are otherwise
immutable. So this admin does not use the deactivate/reactivate ``ServiceBackedModelAdmin``
base — it composes the shared ``ServiceAdminMixin`` (audit context, persisted-instance
sync, DomainError trap) onto Unfold's ModelAdmin directly:

* add  → ``assign_tag`` (idempotent; a repeat is reported, not duplicated)
* change → denied (assignments are immutable)
* remove → an explicit atomic ``Remove selected`` action through ``remove_tag_assignment``
  (the stock hard-delete route is disabled)
"""

from __future__ import annotations

from django import forms
from django.contrib import admin, messages
from django.db import transaction
from unfold.admin import ModelAdmin as UnfoldModelAdmin

from octonomy.assignments.models import TagAssignment
from octonomy.assignments.services import assign_tag, remove_tag_assignment
from octonomy.core.admin_base import (
    SERVICE_ERRORS,
    ServiceAdminMixin,
    format_service_error,
    run_serializer_validator,
)
from octonomy.core.selectors import scope_context_from_values
from octonomy.core.validators import validate_external_id, validate_slug_like

_BLANKABLE_TO_NULL = ("namespace_type", "namespace_id")


class TagAssignmentAdminForm(forms.ModelForm):
    class Meta:
        model = TagAssignment
        fields = (
            "tenant_id",
            "application_id",
            "namespace_type",
            "namespace_id",
            "tag",
            "resource_type",
            "resource_id",
            "assigned_by",
        )

    def clean(self):
        cleaned = super().clean()
        for field in _BLANKABLE_TO_NULL:
            if field in cleaned and cleaned[field] in ("", None):
                cleaned[field] = None
        return cleaned

    def clean_application_id(self):
        return run_serializer_validator(
            validate_external_id, self.cleaned_data["application_id"], "application_id"
        )

    def clean_resource_type(self):
        return run_serializer_validator(
            validate_slug_like, self.cleaned_data["resource_type"], "resource_type"
        )

    def clean_resource_id(self):
        return run_serializer_validator(
            validate_external_id, self.cleaned_data["resource_id"], "resource_id"
        )


@admin.register(TagAssignment)
class TagAssignmentAdmin(ServiceAdminMixin, UnfoldModelAdmin):
    form = TagAssignmentAdminForm
    list_display = (
        "resource_type",
        "resource_id",
        "tag",
        "application_id",
        "scope_label",
        "assigned_by",
        "assigned_at",
    )
    list_filter = ("application_id", "namespace_type", "resource_type")
    search_fields = ("resource_id", "resource_type")
    ordering = ("-assigned_at", "id")
    autocomplete_fields = ("tag",)
    list_select_related = ("tag",)
    actions = ("remove_selected",)

    @admin.display(description="Scope")
    def scope_label(self, obj) -> str:
        if obj.namespace_type:
            return f"{obj.namespace_type}:{obj.namespace_id}"
        return "global"

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("tag")

    # Assignments are immutable and never hard-deleted through the stock route.
    def has_change_permission(self, request, obj=None) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False

    def get_readonly_fields(self, request, obj=None):
        # Only reached on the (view-only) change page; assignment scope and identity
        # are fixed once created.
        if obj is not None:
            return (
                "tenant_id",
                "application_id",
                "namespace_type",
                "namespace_id",
                "tag",
                "resource_type",
                "resource_id",
                "assigned_by",
                "assigned_at",
            )
        return ()

    def get_fields(self, request, obj=None):
        base = [
            "tenant_id",
            "application_id",
            "namespace_type",
            "namespace_id",
            "tag",
            "resource_type",
            "resource_id",
            "assigned_by",
        ]
        if obj is not None:
            base.append("assigned_at")
        return base

    def save_model(self, request, obj, form, change):
        # Only add is reachable (change permission is denied). Route through assign_tag
        # so the write is idempotent and carries audit/outbox/kill-switch behaviour.
        data = form.cleaned_data
        _operation_id, audit_context = self.new_operation(request)
        scope_context = scope_context_from_values(
            data.get("namespace_type"), data.get("namespace_id")
        )
        result = assign_tag(
            tenant_id=data["tenant_id"],
            application_id=data["application_id"],
            tag=data["tag"],
            resource_type=data["resource_type"],
            resource_id=data["resource_id"],
            assigned_by=(data.get("assigned_by") or None),
            audit_context=audit_context,
            scope_context=scope_context,
            include_global=True,
        )
        self.sync_persisted_instance(obj, result.assignment)
        if not result.created:
            messages.warning(
                request,
                "An identical assignment already existed; the request was idempotent "
                "and no new assignment or audit record was created.",
            )

    @admin.action(description="Remove selected (delete + audit)")
    def remove_selected(self, request, queryset):
        rows = list(queryset)
        _operation_id, audit_context = self.new_operation(request)
        try:
            with transaction.atomic():
                for obj in rows:
                    remove_tag_assignment(
                        tenant_id=obj.tenant_id,
                        application_id=obj.application_id,
                        tag_id=obj.tag_id,
                        resource_type=obj.resource_type,
                        resource_id=obj.resource_id,
                        audit_context=audit_context,
                        scope_context=scope_context_from_values(
                            obj.namespace_type, obj.namespace_id
                        ),
                    )
        except SERVICE_ERRORS as exc:
            messages.error(
                request,
                f"No assignments were removed; the selection was rolled back. "
                f"{format_service_error(exc)}",
            )
            return
        messages.success(request, f"{len(rows)} assignment(s) removed.")
