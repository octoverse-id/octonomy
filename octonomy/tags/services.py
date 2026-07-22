from __future__ import annotations

from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework import serializers

from octonomy.audit.services import create_audit_log, tag_snapshot
from octonomy.core.audit import AuditContext
from octonomy.core.auth import GLOBAL_SCOPE, ScopeContext, guard_namespace_write_enabled
from octonomy.core.errors import ConflictError, DomainError
from octonomy.core.metrics import emit_namespace_conflict
from octonomy.core.selectors import (
    guard_scope_immutable,
    namespace_fields,
    row_matches_scope,
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
    guard_namespace_write_enabled(scope_context)
    validate_tag_parent(tenant_id, data.get("application_id"), data.get("parent"), scope_context)
    validate_tag_vocabulary(
        tenant_id,
        data.get("application_id"),
        data.get("vocabulary"),
        scope_context,
    )
    with transaction.atomic():
        # Scope the IntegrityError -> duplicate-slug translation (and the metric) to the
        # entity write only, via a savepoint. An integrity error from the audit/outbox
        # writes below is not a slug collision, so it must propagate rather than be
        # mislabelled as a 409 (and must not emit the duplicate-key metric).
        try:
            with transaction.atomic():
                tag = Tag.objects.create(**data)
        except IntegrityError as exc:
            emit_namespace_conflict(exc, "tag", scope_context)
            raise ConflictError(
                "An active tag with this tenant, application, type, and slug already exists.",
                {"slug": ["Duplicate active tag slug."]},
            )
        create_audit_log(
            tenant_id=tenant_id,
            application_id=tag.application_id,
            **namespace_fields(tag),
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
            **namespace_fields(tag),
            event_type="tag.created",
            aggregate_type="tag",
            aggregate_id=str(tag.id),
            tag_id=tag.id,
            audit_context=audit_context,
            payload={"after": tag_snapshot(tag)},
        )
        return tag


def update_tag(
    tag: Tag,
    data: dict,
    audit_context: AuditContext | None = None,
) -> Tag:
    # Build scope from the row's own (current) namespace, which is always well-formed.
    # Scope is immutable, so this is also the destination — no ScopeContext is built
    # from the request payload, whose one-sided namespace could raise a ValueError.
    scope_context = scope_context_from_values(tag.namespace_type, tag.namespace_id)
    # Write kill-switch first: a namespaced row can't be written while writes are off,
    # so that 403 takes precedence over the scope guard. Then reject any scope change
    # (application_id or namespace) BEFORE validating relations, so a scope-changing
    # PATCH is a deterministic 409 scope_immutable regardless of attachments -- moving
    # a tag would orphan its assignments, aliases, and child links and can silently
    # reassign merchant data (NS-1). Re-create in the target scope instead.
    guard_namespace_write_enabled(scope_context)
    guard_scope_immutable(tag, data)
    application_id = tag.application_id
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

    with transaction.atomic():
        # See create_tag: only the entity write is translated to a duplicate-slug 409
        # (and metric); audit/outbox integrity errors propagate untouched.
        try:
            with transaction.atomic():
                tag.save()
        except IntegrityError as exc:
            emit_namespace_conflict(exc, "tag", scope_context)
            raise ConflictError(
                "An active tag with this tenant, application, type, and slug already exists.",
                {"slug": ["Duplicate active tag slug."]},
            )
        create_audit_log(
            tenant_id=tag.tenant_id,
            application_id=tag.application_id,
            **namespace_fields(tag),
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
            **namespace_fields(tag),
            event_type="tag.updated",
            aggregate_type="tag",
            aggregate_id=str(tag.id),
            tag_id=tag.id,
            audit_context=audit_context,
            payload={"before": changed_before, "after": changed_after},
        )
    return tag


def deactivate_tag(tag: Tag, audit_context: AuditContext | None = None) -> bool:
    guard_namespace_write_enabled(scope_context_from_values(tag.namespace_type, tag.namespace_id))
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
        # The cascade below mutates every active alias for this tag, including
        # namespaced aliases that point at a global tag. Deactivating a global tag
        # therefore writes namespaced rows, so the kill-switch must gate the cascade
        # too — the tag's own scope guard above does not cover it. A namespaced alias
        # while writes are off rejects the whole (atomic) deactivation.
        for alias in active_aliases:
            guard_namespace_write_enabled(
                scope_context_from_values(alias.namespace_type, alias.namespace_id)
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
                **namespace_fields(alias),
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
            **namespace_fields(locked_tag),
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
            **namespace_fields(locked_tag),
            event_type="tag.deactivated",
            aggregate_type="tag",
            aggregate_id=str(locked_tag.id),
            tag_id=locked_tag.id,
            audit_context=audit_context,
            payload=changes,
        )
        create_outbox_events(alias_events)
    return True
