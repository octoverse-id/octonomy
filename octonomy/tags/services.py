from __future__ import annotations

from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework import serializers

from octonomy.audit.services import create_audit_log, tag_snapshot
from octonomy.core.audit import AuditContext
from octonomy.core.auth import GLOBAL_SCOPE, ScopeContext
from octonomy.core.errors import ConflictError, DomainError
from octonomy.core.selectors import (
    namespace_changed,
    row_matches_scope,
    scope_context_from_instance_data,
    scope_context_from_values,
)
from octonomy.events.services import build_outbox_event, create_outbox_event, create_outbox_events
from octonomy.tags.models import Tag, TagAlias


def validate_metadata(value) -> dict:
    if not isinstance(value, dict):
        raise serializers.ValidationError({"metadata": "Metadata must be a JSON object."})
    return value


def validate_tag_parent(
    tenant_id: str,
    application_id: str | None,
    parent: Tag | None,
    scope_context: ScopeContext = GLOBAL_SCOPE,
) -> None:
    if parent is None:
        return

    if parent.tenant_id != tenant_id:
        raise DomainError(
            "Parent tag must belong to the same tenant.", {"parent_id": ["Invalid tenant."]}
        )

    # Shared tags must remain portable across applications in the tenant. Linking
    # a shared tag to an app-specific parent would quietly make the hierarchy
    # application-specific while the tag still looks shared to assignment code.
    if application_id is None and parent.application_id is not None:
        raise DomainError(
            "Shared tags can only use shared parent tags.",
            {"parent_id": ["Parent must also be shared."]},
        )

    if application_id is not None and parent.application_id not in {None, application_id}:
        raise DomainError(
            "Application tags can only use shared or same-application parent tags.",
            {"parent_id": ["Parent application is incompatible."]},
        )

    if not row_matches_scope(parent, scope_context, include_global=True):
        if scope_context.is_global:
            raise DomainError(
                "Global tags can only use global parent tags.",
                {"parent_id": ["Parent namespace is incompatible."]},
            )
        raise DomainError(
            "Namespaced tags can only use global or same-namespace parent tags.",
            {"parent_id": ["Parent namespace is incompatible."]},
        )


def validate_tag_vocabulary(
    tenant_id: str,
    application_id: str | None,
    vocabulary,
    scope_context: ScopeContext = GLOBAL_SCOPE,
    *,
    require_active: bool = True,
) -> None:
    if vocabulary is None:
        return

    if vocabulary.tenant_id != tenant_id:
        raise DomainError(
            "Vocabulary must belong to the same tenant.",
            {"vocabulary_id": ["Invalid tenant."]},
        )

    if require_active and not vocabulary.is_active:
        raise DomainError(
            "Inactive vocabularies cannot be assigned to tags.",
            {"vocabulary_id": ["Inactive vocabularies cannot be assigned to tags."]},
        )

    # Vocabulary compatibility mirrors tag compatibility: shared tags can only
    # depend on shared vocabularies, while app-specific tags may use shared or
    # same-application vocabularies.
    if application_id is None and vocabulary.application_id is not None:
        raise DomainError(
            "Shared tags can only use shared vocabularies.",
            {"vocabulary_id": ["Shared tags can only use shared vocabularies."]},
        )

    if application_id is not None and vocabulary.application_id not in {None, application_id}:
        raise DomainError(
            "Application tags can only use shared or same-application vocabularies.",
            {"vocabulary_id": ["Vocabulary application is incompatible."]},
        )

    if not row_matches_scope(vocabulary, scope_context, include_global=True):
        if scope_context.is_global:
            raise DomainError(
                "Global tags can only use global vocabularies.",
                {"vocabulary_id": ["Vocabulary namespace is incompatible."]},
            )
        raise DomainError(
            "Namespaced tags can only use global or same-namespace vocabularies.",
            {"vocabulary_id": ["Vocabulary namespace is incompatible."]},
        )


def scope_context_from_create_data(data: dict) -> ScopeContext:
    return scope_context_from_values(data.get("namespace_type"), data.get("namespace_id"))


def namespace_or_application_changed(tag: Tag, data: dict) -> bool:
    return (
        ("application_id" in data and data["application_id"] != tag.application_id)
        or namespace_changed(tag, data)
    )


def block_tag_scope_move_if_attached(tag: Tag) -> None:
    if tag.assignments.exists():
        raise ConflictError(
            "Cannot change scope for a tag with assignments.",
            {"application_id": ["Remove assignments before changing tag scope."]},
        )
    if tag.aliases.exists():
        raise ConflictError(
            "Cannot change scope for a tag with aliases.",
            {"application_id": ["Remove aliases before changing tag scope."]},
        )
    if tag.children.exists():
        raise ConflictError(
            "Cannot change scope for a tag with child tags.",
            {"application_id": ["Remove child tags before changing tag scope."]},
        )


def serialize_related_audit_value(field: str, value):
    if field in {"parent", "vocabulary"} and value:
        return str(value.id)
    return value


def create_tag(
    tenant_id: str,
    data: dict,
    audit_context: AuditContext | None = None,
) -> Tag:
    data["tenant_id"] = tenant_id
    scope_context = scope_context_from_create_data(data)
    validate_tag_parent(tenant_id, data.get("application_id"), data.get("parent"), scope_context)
    validate_tag_vocabulary(
        tenant_id,
        data.get("application_id"),
        data.get("vocabulary"),
        scope_context,
    )
    try:
        with transaction.atomic():
            tag = Tag.objects.create(**data)
            create_audit_log(
                tenant_id=tenant_id,
                application_id=tag.application_id,
                action="tag.created",
                entity_type="tag",
                entity_id=str(tag.id),
                tag_id=tag.id,
                audit_context=audit_context,
                changes={"after": tag_snapshot(tag)},
            )
            create_outbox_event(
                tenant_id=tenant_id,
                application_id=tag.application_id,
                event_type="tag.created",
                aggregate_type="tag",
                aggregate_id=str(tag.id),
                tag_id=tag.id,
                audit_context=audit_context,
                payload={"after": tag_snapshot(tag)},
            )
            return tag
    except IntegrityError:
        raise ConflictError(
            "An active tag with this tenant, application, type, and slug already exists.",
            {"slug": ["Duplicate active tag slug."]},
        )


def update_tag(
    tag: Tag,
    data: dict,
    audit_context: AuditContext | None = None,
) -> Tag:
    application_id = data.get("application_id", tag.application_id)
    scope_context = scope_context_from_instance_data(tag, data)
    parent = data.get("parent", tag.parent)
    vocabulary = data.get("vocabulary", tag.vocabulary)
    vocabulary_changed = "vocabulary" in data and data["vocabulary"] != tag.vocabulary
    validate_tag_parent(tag.tenant_id, application_id, parent, scope_context)
    validate_tag_vocabulary(
        tag.tenant_id,
        application_id,
        vocabulary,
        scope_context,
        require_active=vocabulary_changed,
    )

    if namespace_or_application_changed(tag, data):
        # Assignments, aliases, and child links all encode the current tag
        # scope. Moving the tag would leave attached rows legal in the old scope
        # but illegal for the new one, so callers must detach them first.
        block_tag_scope_move_if_attached(tag)

    changed_before = {}
    changed_after = {}
    for field, value in data.items():
        current_value = getattr(tag, field)
        if current_value == value:
            continue
        audit_field = f"{field}_id" if field in {"parent", "vocabulary"} else field
        changed_before[audit_field] = serialize_related_audit_value(field, current_value)
        changed_after[audit_field] = serialize_related_audit_value(field, value)
        setattr(tag, field, value)

    if not changed_before:
        return tag

    try:
        with transaction.atomic():
            tag.save()
            create_audit_log(
                tenant_id=tag.tenant_id,
                application_id=tag.application_id,
                action="tag.updated",
                entity_type="tag",
                entity_id=str(tag.id),
                tag_id=tag.id,
                audit_context=audit_context,
                changes={"before": changed_before, "after": changed_after},
            )
            create_outbox_event(
                tenant_id=tag.tenant_id,
                application_id=tag.application_id,
                event_type="tag.updated",
                aggregate_type="tag",
                aggregate_id=str(tag.id),
                tag_id=tag.id,
                audit_context=audit_context,
                payload={"before": changed_before, "after": changed_after},
            )
    except IntegrityError:
        raise ConflictError(
            "An active tag with this tenant, application, type, and slug already exists.",
            {"slug": ["Duplicate active tag slug."]},
        )
    return tag


def deactivate_tag(tag: Tag, audit_context: AuditContext | None = None) -> bool:
    with transaction.atomic():
        # Product deletion is soft deletion. Lock the row so concurrent deletes
        # observe one state transition and produce at most one deactivation audit
        # event for the tag.
        locked_tag = Tag.objects.select_for_update().get(id=tag.id)
        if not locked_tag.is_active:
            tag.is_active = False
            return False

        locked_tag.is_active = False
        locked_tag.save(update_fields=["is_active", "updated_at"])
        tag.is_active = False
        active_aliases = list(
            TagAlias.objects.select_for_update().filter(
                tenant_id=locked_tag.tenant_id,
                tag=locked_tag,
                is_active=True,
            )
        )
        # A deactivated canonical tag cannot have assignable aliases left behind;
        # cascade by deactivation rather than hard delete so alias history remains
        # visible in audit trails and outbox events can describe the cascade.
        active_alias_ids = [alias.id for alias in active_aliases]
        cascaded_alias_ids = [str(alias_id) for alias_id in active_alias_ids]
        if active_alias_ids:
            TagAlias.objects.filter(id__in=active_alias_ids).update(
                is_active=False,
                updated_at=timezone.now(),
            )
        alias_events = [
            build_outbox_event(
                tenant_id=alias.tenant_id,
                application_id=alias.application_id,
                event_type="tag_alias.deactivated",
                aggregate_type="tag_alias",
                aggregate_id=str(alias.id),
                tag_id=alias.tag_id,
                audit_context=audit_context,
                payload={
                    "before": {"is_active": True},
                    "after": {"is_active": False},
                    "cascade": {
                        "source_event_type": "tag.deactivated",
                        "source_tag_id": str(locked_tag.id),
                    },
                },
            )
            for alias in active_aliases
        ]
        changes = {
            "before": {"is_active": True},
            "after": {"is_active": False},
        }
        if cascaded_alias_ids:
            changes["cascaded_alias_ids"] = cascaded_alias_ids
        create_audit_log(
            tenant_id=locked_tag.tenant_id,
            application_id=locked_tag.application_id,
            action="tag.deactivated",
            entity_type="tag",
            entity_id=str(locked_tag.id),
            tag_id=locked_tag.id,
            audit_context=audit_context,
            changes=changes,
        )
        create_outbox_event(
            tenant_id=locked_tag.tenant_id,
            application_id=locked_tag.application_id,
            event_type="tag.deactivated",
            aggregate_type="tag",
            aggregate_id=str(locked_tag.id),
            tag_id=locked_tag.id,
            audit_context=audit_context,
            payload=changes,
        )
        create_outbox_events(alias_events)
    return True
