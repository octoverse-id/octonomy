from __future__ import annotations

from django.db import IntegrityError, transaction

from octonomy.audit.services import create_audit_log, vocabulary_snapshot
from octonomy.core.audit import AuditContext
from octonomy.core.auth import guard_namespace_write_enabled
from octonomy.core.errors import ConflictError
from octonomy.core.metrics import emit_namespace_conflict
from octonomy.core.selectors import (
    namespace_changed,
    namespace_fields,
    scope_context_from_instance_data,
    scope_context_from_values,
)
from octonomy.events.services import create_outbox_event
from octonomy.tags.models import Vocabulary
from octonomy.tags.services import validate_metadata


def create_vocabulary(
    tenant_id: str,
    data: dict,
    audit_context: AuditContext | None = None,
) -> Vocabulary:
    data["tenant_id"] = tenant_id
    validate_metadata(data.get("metadata", {}))
    scope_context = scope_context_from_values(data.get("namespace_type"), data.get("namespace_id"))
    guard_namespace_write_enabled(scope_context)
    with transaction.atomic():
        # Only the entity write is translated to a duplicate-slug 409 (and metric);
        # audit/outbox integrity errors propagate untouched (see tags.create_tag).
        try:
            with transaction.atomic():
                vocabulary = Vocabulary.objects.create(**data)
        except IntegrityError as exc:
            emit_namespace_conflict(exc, "vocabulary", scope_context)
            raise ConflictError(
                "An active vocabulary with this tenant, application, and slug already exists.",
                {"slug": ["Duplicate active vocabulary slug."]},
            )
        create_audit_log(
            tenant_id=tenant_id,
            application_id=vocabulary.application_id,
            **namespace_fields(vocabulary),
            action="vocabulary.created",
            entity_type="vocabulary",
            entity_id=str(vocabulary.id),
            audit_context=audit_context,
            changes={"after": vocabulary_snapshot(vocabulary)},
        )
        create_outbox_event(
            tenant_id=tenant_id,
            application_id=vocabulary.application_id,
            **namespace_fields(vocabulary),
            event_type="vocabulary.created",
            aggregate_type="vocabulary",
            aggregate_id=str(vocabulary.id),
            audit_context=audit_context,
            payload={"after": vocabulary_snapshot(vocabulary)},
        )
        return vocabulary


def update_vocabulary(
    vocabulary: Vocabulary,
    data: dict,
    audit_context: AuditContext | None = None,
) -> Vocabulary:
    if "metadata" in data:
        validate_metadata(data["metadata"])

    # Guard the current scope as well as the destination so a namespaced->global move
    # cannot slip past the kill-switch (see update_tag).
    scope_context = scope_context_from_instance_data(vocabulary, data)
    guard_namespace_write_enabled(
        scope_context_from_values(vocabulary.namespace_type, vocabulary.namespace_id)
    )
    guard_namespace_write_enabled(scope_context)

    application_changed = (
        "application_id" in data and data["application_id"] != vocabulary.application_id
    )
    scope_changed = namespace_changed(vocabulary, data)
    if application_changed or scope_changed:
        if vocabulary.tags.exists():
            if application_changed and not scope_changed:
                raise ConflictError(
                    "Cannot change application_id for a vocabulary with tags.",
                    {"application_id": ["Remove tags before changing application scope."]},
                )
            raise ConflictError(
                "Cannot change scope for a vocabulary with tags.",
                {"application_id": ["Remove tags before changing vocabulary scope."]},
            )

    changed_before = {}
    changed_after = {}
    for field, value in data.items():
        current_value = getattr(vocabulary, field)
        if current_value == value:
            continue
        changed_before[field] = current_value
        changed_after[field] = value
        setattr(vocabulary, field, value)

    if not changed_before:
        return vocabulary

    with transaction.atomic():
        # Only the entity write is translated to a duplicate-slug 409 (and metric);
        # audit/outbox integrity errors propagate untouched (see tags.create_tag).
        try:
            with transaction.atomic():
                vocabulary.save()
        except IntegrityError as exc:
            emit_namespace_conflict(exc, "vocabulary", scope_context)
            raise ConflictError(
                "An active vocabulary with this tenant, application, and slug already exists.",
                {"slug": ["Duplicate active vocabulary slug."]},
            )
        create_audit_log(
            tenant_id=vocabulary.tenant_id,
            application_id=vocabulary.application_id,
            **namespace_fields(vocabulary),
            action="vocabulary.updated",
            entity_type="vocabulary",
            entity_id=str(vocabulary.id),
            audit_context=audit_context,
            changes={"before": changed_before, "after": changed_after},
        )
        create_outbox_event(
            tenant_id=vocabulary.tenant_id,
            application_id=vocabulary.application_id,
            **namespace_fields(vocabulary),
            event_type="vocabulary.updated",
            aggregate_type="vocabulary",
            aggregate_id=str(vocabulary.id),
            audit_context=audit_context,
            payload={"before": changed_before, "after": changed_after},
        )
    return vocabulary


def deactivate_vocabulary(
    vocabulary: Vocabulary,
    audit_context: AuditContext | None = None,
) -> bool:
    guard_namespace_write_enabled(
        scope_context_from_values(vocabulary.namespace_type, vocabulary.namespace_id)
    )
    if not vocabulary.is_active:
        return False
    vocabulary.is_active = False
    with transaction.atomic():
        vocabulary.save(update_fields=["is_active", "updated_at"])
        create_audit_log(
            tenant_id=vocabulary.tenant_id,
            application_id=vocabulary.application_id,
            **namespace_fields(vocabulary),
            action="vocabulary.deactivated",
            entity_type="vocabulary",
            entity_id=str(vocabulary.id),
            audit_context=audit_context,
            changes={"before": {"is_active": True}, "after": {"is_active": False}},
        )
        create_outbox_event(
            tenant_id=vocabulary.tenant_id,
            application_id=vocabulary.application_id,
            **namespace_fields(vocabulary),
            event_type="vocabulary.deactivated",
            aggregate_type="vocabulary",
            aggregate_id=str(vocabulary.id),
            audit_context=audit_context,
            payload={"before": {"is_active": True}, "after": {"is_active": False}},
        )
    return True
