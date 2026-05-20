from __future__ import annotations

from django.db import IntegrityError, transaction
from rest_framework import serializers

from octonomy.audit.services import create_audit_log, tag_snapshot
from octonomy.core.audit import AuditContext
from octonomy.core.errors import ConflictError, DomainError
from octonomy.tags.models import Tag


def validate_metadata(value) -> dict:
    if not isinstance(value, dict):
        raise serializers.ValidationError({"metadata": "Metadata must be a JSON object."})
    return value


def validate_tag_parent(tenant_id: str, application_id: str | None, parent: Tag | None) -> None:
    if parent is None:
        return

    if parent.tenant_id != tenant_id:
        raise DomainError(
            "Parent tag must belong to the same tenant.", {"parent_id": ["Invalid tenant."]}
        )

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


def create_tag(
    tenant_id: str,
    data: dict,
    audit_context: AuditContext | None = None,
) -> Tag:
    data["tenant_id"] = tenant_id
    validate_tag_parent(tenant_id, data.get("application_id"), data.get("parent"))
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
    parent = data.get("parent", tag.parent)
    validate_tag_parent(tag.tenant_id, application_id, parent)

    if "application_id" in data and data["application_id"] != tag.application_id:
        if tag.assignments.exists():
            raise ConflictError(
                "Cannot change application_id for a tag with assignments.",
                {"application_id": ["Remove assignments before changing application scope."]},
            )

    changed_before = {}
    changed_after = {}
    for field, value in data.items():
        current_value = getattr(tag, field)
        if current_value == value:
            continue
        audit_field = "parent_id" if field == "parent" else field
        changed_before[audit_field] = (
            str(current_value.id) if field == "parent" and current_value else current_value
        )
        changed_after[audit_field] = str(value.id) if field == "parent" and value else value
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
    except IntegrityError:
        raise ConflictError(
            "An active tag with this tenant, application, type, and slug already exists.",
            {"slug": ["Duplicate active tag slug."]},
        )
    return tag


def deactivate_tag(tag: Tag, audit_context: AuditContext | None = None) -> bool:
    if not tag.is_active:
        return False

    tag.is_active = False
    with transaction.atomic():
        tag.save(update_fields=["is_active", "updated_at"])
        create_audit_log(
            tenant_id=tag.tenant_id,
            application_id=tag.application_id,
            action="tag.deactivated",
            entity_type="tag",
            entity_id=str(tag.id),
            tag_id=tag.id,
            audit_context=audit_context,
            changes={"before": {"is_active": True}, "after": {"is_active": False}},
        )
    return True
