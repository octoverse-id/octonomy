"""Shared base classes for the opt-in Octonomy operator admin (#85).

The admin is a *thin presentation layer* over the existing domain services. Every
mutation is routed through ``create_*``/``update_*``/``deactivate_*``/``assign_tag``/
``remove_tag_assignment``, which already enforce tenant/application/namespace
compatibility, the namespaced-write kill-switch, soft deletion, idempotency, and the
audit + outbox side effects. The admin adds no parallel domain logic — it wires Django
admin into those services and renders their rejections as actionable admin errors.

Two bases live here:

* ``ServiceBackedModelAdmin`` — writable taxonomy models (Vocabulary/Tag/TagAlias).
  It routes create/update through the service layer, traps ``DomainError`` and DRF
  ``ValidationError`` so a domain rejection re-renders as an admin error instead of an
  HTTP 500, disables Django's hard-delete routes in favour of soft deactivation, and
  supplies atomic bulk Deactivate/Reactivate actions.
* ``ReadOnlyModelAdmin`` — append-only diagnostics (AuditLog/OutboxEvent/ServiceClient/
  ServiceClientGrant). View/list/search/filter only; every mutation surface is denied.

``TagAssignment`` has its own admin (``octonomy.assignments.admin``) built from the
``ServiceAdminMixin`` here, because its lifecycle (idempotent assign / service-backed
hard delete, otherwise immutable) does not fit the deactivate/reactivate model.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from django import forms
from django.contrib import admin, messages
from django.db import transaction
from django.http import HttpRequest, HttpResponseRedirect
from django.utils.html import format_html
from rest_framework import serializers
from unfold.admin import ModelAdmin as UnfoldModelAdmin

from octonomy.core.audit import AuditContext
from octonomy.core.errors import DomainError

# The union of failures a domain service can raise that must surface as an admin
# error rather than a 500. ``DomainError`` covers every Octonomy domain rejection
# (ConflictError, ScopeImmutableError, NamespacedWritesDisabledError, the mismatch
# family). DRF ``ValidationError`` covers the serializer-style validators the services
# reuse (e.g. metadata / assignment resolution).
SERVICE_ERRORS = (DomainError, serializers.ValidationError)


def build_admin_audit_context(request: HttpRequest, operation_id: uuid.UUID) -> AuditContext:
    """Audit context for an admin-originated write.

    The actor is ``admin:<Django username>`` so every audit log and outbox event a
    superuser produces through the console is attributable and clearly distinct from a
    service-token actor (a bare client name). ``request_id`` rides the same
    ``RequestContextMiddleware`` value REST writes use. ``operation_id`` is supplied by
    the caller: one per form submission, and one shared across a whole bulk action so a
    multi-row action is a single correlatable operation.
    """

    user = getattr(request, "user", None)
    username = user.get_username() if user is not None else None
    return AuditContext(
        actor_id=f"admin:{username}" if username else "admin:unknown",
        request_id=getattr(request, "request_id", None),
        operation_id=operation_id,
    )


def flatten_error_detail(detail: Any) -> list[str]:
    """Flatten a DRF/DomainError ``details`` tree into readable ``field: message`` lines."""

    lines: list[str] = []
    if isinstance(detail, dict):
        for key, value in detail.items():
            for message in flatten_error_detail(value):
                if key in ("non_field_errors", "__all__"):
                    lines.append(message)
                else:
                    lines.append(f"{key}: {message}")
    elif isinstance(detail, list | tuple):
        for item in detail:
            lines.extend(flatten_error_detail(item))
    elif detail not in (None, ""):
        lines.append(str(detail))
    return lines


def format_service_error(exc: Exception) -> str:
    """Render a trapped service error as a single actionable admin message."""

    if isinstance(exc, DomainError):
        parts = flatten_error_detail(exc.details)
        return f"{exc.message} ({'; '.join(parts)})" if parts else exc.message
    parts = flatten_error_detail(getattr(exc, "detail", None))
    return "; ".join(parts) if parts else "The request could not be completed."


def pretty_json(value: Any) -> str:
    """Render a JSON payload/metadata/scopes value readably in a read-only detail view.

    Deliberately a plain indented ``<pre>`` block — no extra third-party JSON widget is
    introduced for the diagnostics screens (issue #85).
    """

    try:
        text = json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = str(value)
    return format_html('<pre style="white-space:pre-wrap;margin:0">{}</pre>', text)


def run_serializer_validator(func, value, field_name):
    """Run a REST serializer validator inside a Django form ``clean_*`` method.

    The taxonomy REST serializers validate slug/type/external-id shape and the
    JSON-object metadata rule with the shared ``octonomy.core.validators`` helpers,
    which raise DRF ``serializers.ValidationError``. Reusing the *same* validators here
    (rather than re-implementing the regex) keeps the admin's input rules identical to
    the API's; this adapter translates the DRF error into the ``forms.ValidationError``
    the admin form machinery expects.
    """

    try:
        func(value, field_name)
    except serializers.ValidationError as exc:
        raise forms.ValidationError(flatten_error_detail(exc.detail) or [str(exc)])
    return value


class ServiceAdminMixin:
    """Shared plumbing for admins whose writes go through a domain service.

    Provides the audit-context helper, the persisted-instance sync (so Django's admin
    log entry and post-save redirect use the *real* row the service returned, not the
    unsaved form instance), and the changeform-level trap that converts a service
    rejection into an admin message + reload instead of a 500.
    """

    def new_operation(self, request: HttpRequest) -> tuple[uuid.UUID, AuditContext]:
        operation_id = uuid.uuid4()
        return operation_id, build_admin_audit_context(request, operation_id)

    @staticmethod
    def sync_persisted_instance(target, source) -> None:
        """Copy the service's persisted row onto the admin's form instance.

        Django builds ``obj`` from the submitted form (unsaved), then uses it for
        ``log_addition``/``log_change`` and the post-save redirect. The service is what
        actually persists (and, for create, assigns the real primary key), so mirror the
        returned row's concrete fields — including the pk — back onto ``obj`` and mark it
        as no-longer-adding so the admin references the true saved state.
        """

        if source is None:
            return
        for field in source._meta.concrete_fields:
            setattr(target, field.attname, getattr(source, field.attname))
        target._state.adding = False
        target._state.db = source._state.db

    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        # Django wraps the POST branch of the changeform in transaction.atomic, so when a
        # service raises the whole submission (entity + audit + outbox) rolls back — no
        # partial write, no phantom event. We catch it here (outside that atomic, once the
        # rollback is complete) and reload the form with an actionable banner rather than
        # letting the exception become a 500. Common input mistakes (bad slug, duplicate
        # active slug, malformed namespace) are already caught inline by form/model
        # validation before save_model runs; this trap is for the domain invariants only
        # the service enforces (cross-scope relations, the write kill-switch).
        try:
            return super().changeform_view(request, object_id, form_url, extra_context)
        except SERVICE_ERRORS as exc:
            messages.error(request, format_service_error(exc))
            return HttpResponseRedirect(request.get_full_path())


class ServiceBackedModelAdmin(ServiceAdminMixin, UnfoldModelAdmin):
    """Writable taxonomy admin base (Vocabulary/Tag/TagAlias).

    Subclasses implement the four service bridges below. All create/update flows and
    the Deactivate/Reactivate actions route through them, so audit/outbox/kill-switch
    behaviour is identical to the REST surface.
    """

    # Subclasses MUST implement these bridges to the domain services.
    def service_create(self, request: HttpRequest, form, audit_context: AuditContext):
        raise NotImplementedError

    def service_update(self, request: HttpRequest, obj, form, audit_context: AuditContext):
        raise NotImplementedError

    def service_deactivate(self, request: HttpRequest, obj, audit_context: AuditContext) -> None:
        raise NotImplementedError

    def service_reactivate(self, request: HttpRequest, obj, audit_context: AuditContext) -> None:
        raise NotImplementedError

    # --- create / update routing -------------------------------------------------

    def save_model(self, request, obj, form, change):
        # Do NOT let Django persist obj.save(): the service owns the write (transaction,
        # audit, outbox, kill-switch, and — on create — the primary key). Route to it and
        # mirror the persisted row back onto obj for the admin log/redirect.
        _operation_id, audit_context = self.new_operation(request)
        if change:
            persisted = self.service_update(request, obj, form, audit_context)
        else:
            persisted = self.service_create(request, form, audit_context)
        self.sync_persisted_instance(obj, persisted)

    # --- delete surface: soft-delete only ----------------------------------------

    def has_delete_permission(self, request, obj=None) -> bool:
        # Taxonomy rows are never hard-deleted. Hiding the delete permission removes both
        # the single-object Delete route and the stock ``delete_selected`` bulk action;
        # operators use the explicit Deactivate action instead (soft delete, audited).
        return False

    def delete_model(self, request, obj):
        # Defense in depth: even if a subclass re-enables delete, route it through the
        # service so it soft-deactivates (audit + outbox + kill-switch) instead of the
        # stock hard delete that bypasses all of them.
        _operation_id, audit_context = self.new_operation(request)
        self.service_deactivate(request, obj, audit_context)

    def delete_queryset(self, request, queryset):
        # ``delete_selected`` calls delete_queryset (raw QuerySet.delete()); override it
        # too so no bulk hard-delete path can bypass the services.
        _operation_id, audit_context = self.new_operation(request)
        with transaction.atomic():
            for obj in queryset:
                self.service_deactivate(request, obj, audit_context)

    # --- bulk actions: atomic Deactivate / Reactivate ----------------------------

    actions = ("deactivate_selected", "reactivate_selected")

    def get_actions(self, request):
        actions = super().get_actions(request)
        # Belt and suspenders: has_delete_permission=False already drops it, but never
        # let the stock hard-delete action reappear on a service-backed model.
        actions.pop("delete_selected", None)
        return actions

    def _run_bulk(self, request, queryset, service_method, past_tense: str) -> None:
        # One operation_id for the whole selection: a bulk action is a single
        # correlatable operation. Atomic across the set so one rejection rolls the
        # entire selection back — never a partially processed batch.
        rows = list(queryset)
        _operation_id, audit_context = self.new_operation(request)
        try:
            with transaction.atomic():
                for obj in rows:
                    service_method(request, obj, audit_context)
        except SERVICE_ERRORS as exc:
            messages.error(
                request,
                f"No rows were {past_tense}; the selection was rolled back. "
                f"{format_service_error(exc)}",
            )
            return
        messages.success(request, f"{len(rows)} row(s) {past_tense}.")

    @admin.action(description="Deactivate selected (soft delete)")
    def deactivate_selected(self, request, queryset):
        self._run_bulk(request, queryset, self.service_deactivate, "deactivated")

    @admin.action(description="Reactivate selected")
    def reactivate_selected(self, request, queryset):
        self._run_bulk(request, queryset, self.service_reactivate, "reactivated")


class ReadOnlyModelAdmin(UnfoldModelAdmin):
    """View/list/search/filter-only base for append-only diagnostics tables.

    Every mutation surface is denied: add/change/delete permissions are false (so the
    add/change/delete views 403 and no bulk actions are offered), and the changelist
    exposes no actions at all. Subclasses restrict ``list_display``/``search_fields``/
    ``list_filter`` to indexed columns so a scan over an unbounded table stays fast.
    """

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False

    def get_actions(self, request):
        # No bulk actions whatsoever (also guarantees delete_selected is absent).
        return {}
