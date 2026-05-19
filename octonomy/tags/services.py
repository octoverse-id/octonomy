from __future__ import annotations

from django.db import IntegrityError, transaction
from rest_framework import serializers

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


def create_tag(tenant_id: str, data: dict) -> Tag:
    data["tenant_id"] = tenant_id
    validate_tag_parent(tenant_id, data.get("application_id"), data.get("parent"))
    try:
        with transaction.atomic():
            return Tag.objects.create(**data)
    except IntegrityError:
        raise ConflictError(
            "An active tag with this tenant, application, type, and slug already exists.",
            {"slug": ["Duplicate active tag slug."]},
        )


def update_tag(tag: Tag, data: dict) -> Tag:
    application_id = data.get("application_id", tag.application_id)
    parent = data.get("parent", tag.parent)
    validate_tag_parent(tag.tenant_id, application_id, parent)

    if "application_id" in data and data["application_id"] != tag.application_id:
        if tag.assignments.exists():
            raise ConflictError(
                "Cannot change application_id for a tag with assignments.",
                {"application_id": ["Remove assignments before changing application scope."]},
            )

    for field, value in data.items():
        setattr(tag, field, value)

    try:
        with transaction.atomic():
            tag.save()
    except IntegrityError:
        raise ConflictError(
            "An active tag with this tenant, application, type, and slug already exists.",
            {"slug": ["Duplicate active tag slug."]},
        )
    return tag


def deactivate_tag(tag: Tag) -> None:
    if tag.is_active:
        tag.is_active = False
        tag.save(update_fields=["is_active", "updated_at"])
