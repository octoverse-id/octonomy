from __future__ import annotations

from django.db import IntegrityError, transaction

from octonomy.audit.services import create_audit_log, vocabulary_snapshot
from octonomy.core.audit import AuditContext
from octonomy.core.errors import ConflictError
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
    try:
        with transaction.atomic():
            vocabulary = Vocabulary.objects.create(**data)
            create_audit_log(
                tenant_id=tenant_id,
                application_id=vocabulary.application_id,
                action="vocabulary.created",
                entity_type="vocabulary",
                entity_id=str(vocabulary.id),
                audit_context=audit_context,
                changes={"after": vocabulary_snapshot(vocabulary)},
            )
            create_outbox_event(
                tenant_id=tenant_id,
                application_id=vocabulary.application_id,
                event_type="vocabulary.created",
                aggregate_type="vocabulary",
                aggregate_id=str(vocabulary.id),
                audit_context=audit_context,
                payload={"after": vocabulary_snapshot(vocabulary)},
            )
            return vocabulary
    except IntegrityError:
        raise ConflictError(
            "An active vocabulary with this tenant, application, and slug already exists.",
            {"slug": ["Duplicate active vocabulary slug."]},
        )


def update_vocabulary(
    vocabulary: Vocabulary,
    data: dict,
    audit_context: AuditContext | None = None,
) -> Vocabulary:
    if "metadata" in data:
        validate_metadata(data["metadata"])

    if "application_id" in data and data["application_id"] != vocabulary.application_id:
        if vocabulary.tags.exists():
            raise ConflictError(
                "Cannot change application_id for a vocabulary with tags.",
                {"application_id": ["Remove tags before changing application scope."]},
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

    try:
        with transaction.atomic():
            vocabulary.save()
            create_audit_log(
                tenant_id=vocabulary.tenant_id,
                application_id=vocabulary.application_id,
                action="vocabulary.updated",
                entity_type="vocabulary",
                entity_id=str(vocabulary.id),
                audit_context=audit_context,
                changes={"before": changed_before, "after": changed_after},
            )
            create_outbox_event(
                tenant_id=vocabulary.tenant_id,
                application_id=vocabulary.application_id,
                event_type="vocabulary.updated",
                aggregate_type="vocabulary",
                aggregate_id=str(vocabulary.id),
                audit_context=audit_context,
                payload={"before": changed_before, "after": changed_after},
            )
    except IntegrityError:
        raise ConflictError(
            "An active vocabulary with this tenant, application, and slug already exists.",
            {"slug": ["Duplicate active vocabulary slug."]},
        )
    return vocabulary


def deactivate_vocabulary(
    vocabulary: Vocabulary,
    audit_context: AuditContext | None = None,
) -> bool:
    if not vocabulary.is_active:
        return False
    vocabulary.is_active = False
    with transaction.atomic():
        vocabulary.save(update_fields=["is_active", "updated_at"])
        create_audit_log(
            tenant_id=vocabulary.tenant_id,
            application_id=vocabulary.application_id,
            action="vocabulary.deactivated",
            entity_type="vocabulary",
            entity_id=str(vocabulary.id),
            audit_context=audit_context,
            changes={"before": {"is_active": True}, "after": {"is_active": False}},
        )
        create_outbox_event(
            tenant_id=vocabulary.tenant_id,
            application_id=vocabulary.application_id,
            event_type="vocabulary.deactivated",
            aggregate_type="vocabulary",
            aggregate_id=str(vocabulary.id),
            audit_context=audit_context,
            payload={"before": {"is_active": True}, "after": {"is_active": False}},
        )
    return True
