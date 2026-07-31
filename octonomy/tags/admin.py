"""Service-backed admin for the taxonomy models (Vocabulary / Tag / TagAlias).

Every create/update/deactivate/reactivate routes through the domain services in
``octonomy.tags`` so the admin inherits tenant/application/namespace isolation, the
namespaced-write kill-switch, soft deletion, audit logs, and outbox events unchanged
(see ``octonomy.core.admin_base``). The admin forms only validate *input shape* with
the same shared validators the REST serializers use (slug/type/external-id, and the
JSON-object metadata rule) — the services remain authoritative for every domain
invariant, and their rejections surface as admin messages rather than HTTP 500s.

Scope fields (``tenant_id``/``application_id``/``namespace_type``/``namespace_id``)
and ``is_active`` are read-only on the change form: scope is immutable after creation
(NS-1) and status transitions happen only through the explicit Deactivate/Reactivate
actions, which apply the active-relation guard that a bare ``is_active`` edit would
skip.
"""

from __future__ import annotations

from django import forms
from django.contrib import admin

from octonomy.core.admin_base import (
    ServiceBackedModelAdmin,
    run_serializer_validator,
)
from octonomy.core.validators import validate_external_id, validate_slug_like
from octonomy.tags.alias_services import (
    create_tag_alias,
    deactivate_tag_alias,
    reactivate_tag_alias,
    update_tag_alias,
)
from octonomy.tags.models import Tag, TagAlias, Vocabulary
from octonomy.tags.services import (
    create_tag,
    deactivate_tag,
    reactivate_tag,
    update_tag,
    validate_metadata,
)
from octonomy.tags.vocabulary_services import (
    create_vocabulary,
    deactivate_vocabulary,
    reactivate_vocabulary,
    update_vocabulary,
)

# The scope fields are set at creation and immutable thereafter, so they are read-only
# on the change form. ``is_active`` is read-only there too: it is driven only by the
# Deactivate/Reactivate actions (which carry the active-relation guard).
_IMMUTABLE_ON_CHANGE = ("tenant_id", "application_id", "namespace_type", "namespace_id")
_READONLY_ON_CHANGE = (*_IMMUTABLE_ON_CHANGE, "is_active", "created_at", "updated_at")

# Nullable scope columns come back from an empty admin text input as "" but a global
# row must store NULL (the namespace CHECK constraint rejects blanks), so normalize.
_BLANKABLE_TO_NULL = ("application_id", "namespace_type", "namespace_id")


class _TaxonomyAdminForm(forms.ModelForm):
    """Shared admin form: input-shape validation mirroring the REST serializers."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # metadata is a JSONField with a dict default but blank=False, so Django would
        # render it as required; make it optional and let clean_metadata default it.
        if "metadata" in self.fields:
            self.fields["metadata"].required = False

    def clean(self):
        cleaned = super().clean()
        for field in _BLANKABLE_TO_NULL:
            if field in cleaned and cleaned[field] in ("", None):
                cleaned[field] = None
        return cleaned

    def clean_application_id(self):
        value = self.cleaned_data.get("application_id")
        if value:
            return run_serializer_validator(validate_external_id, value, "application_id")
        return value

    def clean_slug(self):
        return run_serializer_validator(validate_slug_like, self.cleaned_data["slug"], "slug")

    def clean_metadata(self):
        value = self.cleaned_data.get("metadata")
        if value in (None, ""):
            return {}
        run_serializer_validator(lambda v, _field: validate_metadata(v), value, "metadata")
        return value


class _ScopedTaxonomyAdmin(ServiceBackedModelAdmin):
    """Shared list/scope wiring for the three taxonomy admins."""

    list_per_page = 50

    @admin.display(description="Scope")
    def scope_label(self, obj) -> str:
        # Superusers see every partition at once; surface enough scope for two
        # identically named rows in different partitions to be told apart.
        application = obj.application_id or "shared"
        if obj.namespace_type:
            return f"{application} · {obj.namespace_type}:{obj.namespace_id}"
        return f"{application} · global"

    def get_fields(self, request, obj=None):
        base = list(self.add_fields)
        if obj is not None:
            return [*base, "is_active", "created_at", "updated_at"]
        return base

    def get_readonly_fields(self, request, obj=None):
        if obj is not None:
            return _READONLY_ON_CHANGE
        return ()


@admin.register(Vocabulary)
class VocabularyAdmin(_ScopedTaxonomyAdmin):
    add_fields = (
        "tenant_id",
        "application_id",
        "namespace_type",
        "namespace_id",
        "name",
        "slug",
        "description",
        "metadata",
    )
    list_display = ("name", "slug", "tenant_id", "scope_label", "is_active", "updated_at")
    list_filter = ("is_active", "application_id", "namespace_type")
    search_fields = ("slug", "name")
    ordering = ("name", "slug", "id")

    class form(_TaxonomyAdminForm):
        class Meta:
            model = Vocabulary
            fields = (
                "tenant_id",
                "application_id",
                "namespace_type",
                "namespace_id",
                "name",
                "slug",
                "description",
                "metadata",
            )

    def service_create(self, request, form, audit_context):
        data = dict(form.cleaned_data)
        tenant_id = data.pop("tenant_id")
        return create_vocabulary(tenant_id, data, audit_context)

    def service_update(self, request, obj, form, audit_context):
        current = Vocabulary.objects.get(pk=obj.pk)
        return update_vocabulary(current, dict(form.cleaned_data), audit_context)

    def service_deactivate(self, request, obj, audit_context):
        deactivate_vocabulary(obj, audit_context)

    def service_reactivate(self, request, obj, audit_context):
        reactivate_vocabulary(obj, audit_context)


@admin.register(Tag)
class TagAdmin(_ScopedTaxonomyAdmin):
    add_fields = (
        "tenant_id",
        "application_id",
        "namespace_type",
        "namespace_id",
        "name",
        "slug",
        "type",
        "description",
        "parent",
        "vocabulary",
        "metadata",
    )
    list_display = ("name", "slug", "type", "tenant_id", "scope_label", "is_active", "updated_at")
    list_filter = ("is_active", "type", "application_id", "namespace_type")
    search_fields = ("slug", "name", "type")
    ordering = ("name", "slug", "id")
    autocomplete_fields = ("parent", "vocabulary")
    list_select_related = ("parent", "vocabulary")

    class form(_TaxonomyAdminForm):
        class Meta:
            model = Tag
            fields = (
                "tenant_id",
                "application_id",
                "namespace_type",
                "namespace_id",
                "name",
                "slug",
                "type",
                "description",
                "parent",
                "vocabulary",
                "metadata",
            )

        def clean_type(self):
            return run_serializer_validator(validate_slug_like, self.cleaned_data["type"], "type")

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("parent", "vocabulary")

    def service_create(self, request, form, audit_context):
        data = dict(form.cleaned_data)
        tenant_id = data.pop("tenant_id")
        return create_tag(tenant_id, data, audit_context)

    def service_update(self, request, obj, form, audit_context):
        current = Tag.objects.get(pk=obj.pk)
        return update_tag(current, dict(form.cleaned_data), audit_context)

    def service_deactivate(self, request, obj, audit_context):
        # Cascades active aliases by deactivation (auditable), per deactivate_tag.
        deactivate_tag(obj, audit_context)

    def service_reactivate(self, request, obj, audit_context):
        # reactivate_tag re-validates the parent/vocabulary (active, in-scope) under row
        # locks inside its own transaction — the check can't interleave with a concurrent
        # deactivation. Cascaded aliases are intentionally NOT auto-revived.
        reactivate_tag(obj, audit_context)


@admin.register(TagAlias)
class TagAliasAdmin(_ScopedTaxonomyAdmin):
    add_fields = (
        "tenant_id",
        "application_id",
        "namespace_type",
        "namespace_id",
        "tag",
        "name",
        "slug",
        "metadata",
    )
    list_display = ("name", "slug", "tag", "tenant_id", "scope_label", "is_active", "updated_at")
    list_filter = ("is_active", "application_id", "namespace_type")
    search_fields = ("slug", "name")
    ordering = ("name", "slug", "id")
    autocomplete_fields = ("tag",)
    list_select_related = ("tag",)

    class form(_TaxonomyAdminForm):
        class Meta:
            model = TagAlias
            fields = (
                "tenant_id",
                "application_id",
                "namespace_type",
                "namespace_id",
                "tag",
                "name",
                "slug",
                "metadata",
            )

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("tag")

    def service_create(self, request, form, audit_context):
        data = dict(form.cleaned_data)
        tenant_id = data.pop("tenant_id")
        return create_tag_alias(tenant_id, data, audit_context)

    def service_update(self, request, obj, form, audit_context):
        current = TagAlias.objects.select_related("tag").get(pk=obj.pk)
        return update_tag_alias(current, dict(form.cleaned_data), audit_context)

    def service_deactivate(self, request, obj, audit_context):
        deactivate_tag_alias(obj, audit_context)

    def service_reactivate(self, request, obj, audit_context):
        # reactivate_tag_alias re-validates the canonical tag (active, in-scope) with the
        # tag row locked inside its own transaction.
        reactivate_tag_alias(obj, audit_context)
