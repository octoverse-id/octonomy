from __future__ import annotations

from django.db import IntegrityError, transaction
from rest_framework import serializers

from octonomy.audit.services import create_audit_log, tag_alias_snapshot
from octonomy.core.audit import AuditContext
from octonomy.core.errors import ApplicationMismatchError, ConflictError, DomainError
from octonomy.tags.alias_selectors import (
    active_aliases_for_resolution,
    active_tags_for_resolution,
)
from octonomy.tags.models import Tag, TagAlias
from octonomy.tags.services import validate_metadata


def validate_alias_tag(tenant_id: str, application_id: str | None, tag: Tag) -> None:
    if tag.tenant_id != tenant_id:
        raise DomainError(
            "Alias tag must belong to the same tenant.",
            {"tag_id": ["Invalid tenant."]},
        )

    if not tag.is_active:
        raise DomainError(
            "Alias tag must be active.",
            {"tag_id": ["Tag is inactive."]},
        )

    if tag.application_id is not None and application_id != tag.application_id:
        raise ApplicationMismatchError(
            "App-specific tags can only use aliases in the same application.",
            {"application_id": ["Alias application is incompatible with tag."]},
        )


def create_tag_alias(
    tenant_id: str,
    data: dict,
    audit_context: AuditContext | None = None,
) -> TagAlias:
    data["tenant_id"] = tenant_id
    validate_metadata(data.get("metadata", {}))
    try:
        with transaction.atomic():
            data["tag"] = Tag.objects.select_for_update().get(id=data["tag"].id)
            validate_alias_tag(tenant_id, data.get("application_id"), data["tag"])
            alias = TagAlias.objects.create(**data)
            create_audit_log(
                tenant_id=tenant_id,
                application_id=alias.application_id,
                action="tag_alias.created",
                entity_type="tag_alias",
                entity_id=str(alias.id),
                tag_id=alias.tag_id,
                audit_context=audit_context,
                changes={"after": tag_alias_snapshot(alias)},
            )
            return alias
    except IntegrityError:
        raise ConflictError(
            "An active tag alias with this tenant, application, and slug already exists.",
            {"slug": ["Duplicate active alias slug."]},
        )


def update_tag_alias(
    alias: TagAlias,
    data: dict,
    audit_context: AuditContext | None = None,
) -> TagAlias:
    application_id = data.get("application_id", alias.application_id)
    tag = data.get("tag", alias.tag)
    validate_alias_tag(alias.tenant_id, application_id, tag)
    if "metadata" in data:
        validate_metadata(data["metadata"])

    changed_before = {}
    changed_after = {}
    for field, value in data.items():
        current_value = getattr(alias, field)
        if current_value == value:
            continue
        audit_field = "tag_id" if field == "tag" else field
        changed_before[audit_field] = str(current_value.id) if field == "tag" else current_value
        changed_after[audit_field] = str(value.id) if field == "tag" else value
        setattr(alias, field, value)

    if not changed_before:
        return alias

    try:
        with transaction.atomic():
            locked_tag = Tag.objects.select_for_update().get(id=tag.id)
            validate_alias_tag(alias.tenant_id, application_id, locked_tag)
            alias.tag = locked_tag
            alias.save()
            create_audit_log(
                tenant_id=alias.tenant_id,
                application_id=alias.application_id,
                action="tag_alias.updated",
                entity_type="tag_alias",
                entity_id=str(alias.id),
                tag_id=alias.tag_id,
                audit_context=audit_context,
                changes={"before": changed_before, "after": changed_after},
            )
    except IntegrityError:
        raise ConflictError(
            "An active tag alias with this tenant, application, and slug already exists.",
            {"slug": ["Duplicate active alias slug."]},
        )
    return alias


def deactivate_tag_alias(alias: TagAlias, audit_context: AuditContext | None = None) -> bool:
    if not alias.is_active:
        return False

    alias.is_active = False
    with transaction.atomic():
        alias.save(update_fields=["is_active", "updated_at"])
        create_audit_log(
            tenant_id=alias.tenant_id,
            application_id=alias.application_id,
            action="tag_alias.deactivated",
            entity_type="tag_alias",
            entity_id=str(alias.id),
            tag_id=alias.tag_id,
            audit_context=audit_context,
            changes={"before": {"is_active": True}, "after": {"is_active": False}},
        )
    return True


def resolve_tag_reference(
    tenant_id: str,
    slug: str,
    application_id: str | None,
    tag_type: str | None = None,
) -> dict:
    tags = list(active_tags_for_resolution(tenant_id, slug, application_id, tag_type))
    if tags:
        if application_id:
            app_tags = [tag for tag in tags if tag.application_id == application_id]
            if app_tags:
                if len(app_tags) > 1:
                    raise serializers.ValidationError(
                        {"type": ["Multiple canonical tags match this slug; provide type."]}
                    )
                return {"matched_type": "tag", "matched_alias": None, "tag": app_tags[0]}
        if len(tags) > 1:
            raise serializers.ValidationError(
                {"type": ["Multiple canonical tags match this slug; provide type."]}
            )
        return {"matched_type": "tag", "matched_alias": None, "tag": tags[0]}

    alias = active_aliases_for_resolution(tenant_id, slug, application_id).first()
    if alias is None:
        raise serializers.ValidationError({"slug": ["No active tag or alias matched this slug."]})

    return {"matched_type": "alias", "matched_alias": alias, "tag": alias.tag}


def resolve_assignable_alias(
    *,
    tenant_id: str,
    application_id: str,
    alias_id=None,
    alias_slug: str | None = None,
) -> Tag:
    field = "alias_id" if alias_id else "alias_slug"
    if alias_id:
        try:
            alias = TagAlias.objects.for_tenant(tenant_id).select_related("tag").get(id=alias_id)
        except TagAlias.DoesNotExist:
            raise serializers.ValidationError({"alias_id": ["Alias was not found."]})
    else:
        alias = active_aliases_for_resolution(tenant_id, alias_slug, application_id).first()
        if alias is None:
            raise serializers.ValidationError({"alias_slug": ["Alias was not found."]})

    if not alias.is_active:
        raise serializers.ValidationError({field: ["Inactive aliases cannot be assigned."]})

    if not alias.tag.is_active:
        raise serializers.ValidationError({field: ["Alias tag is inactive."]})

    if alias.application_id is not None and alias.application_id != application_id:
        raise ApplicationMismatchError(
            details={"application_id": ["Alias belongs to another application."]}
        )

    return alias.tag
