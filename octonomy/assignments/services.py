from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings
from django.db import IntegrityError, transaction
from rest_framework import serializers

from octonomy.assignments.models import TagAssignment
from octonomy.core.errors import ApplicationMismatchError, InactiveTagError
from octonomy.tags.models import Tag


@dataclass(frozen=True)
class AssignmentResult:
    assignment: TagAssignment
    created: bool


def validate_tag_for_assignment(tag: Tag, tenant_id: str, application_id: str) -> None:
    if tag.tenant_id != tenant_id:
        raise serializers.ValidationError({"tag_id": ["Tag was not found."]})
    if not tag.is_active:
        raise InactiveTagError(details={"tag_id": ["Inactive tags cannot be assigned."]})
    if tag.application_id is not None and tag.application_id != application_id:
        raise ApplicationMismatchError(
            details={"application_id": ["Tag belongs to another application."]}
        )


def get_assignable_tags(tenant_id: str, application_id: str, tag_ids: list) -> list[Tag]:
    max_bulk = getattr(settings, "MAX_BULK_TAGS", 200)
    if len(tag_ids) > max_bulk:
        raise serializers.ValidationError({"tag_ids": [f"Maximum bulk size is {max_bulk}."]})

    unique_ids = list(dict.fromkeys(tag_ids))
    tags = list(Tag.objects.for_tenant(tenant_id).filter(id__in=unique_ids))
    tags_by_id = {tag.id: tag for tag in tags}
    missing = [str(tag_id) for tag_id in unique_ids if tag_id not in tags_by_id]
    if missing:
        raise serializers.ValidationError({"tag_ids": [f"Unknown tag ids: {', '.join(missing)}"]})

    for tag in tags:
        validate_tag_for_assignment(tag, tenant_id, application_id)
    return tags


def assign_tag(
    tenant_id: str,
    application_id: str,
    tag: Tag,
    resource_type: str,
    resource_id: str,
    assigned_by: str | None = None,
) -> AssignmentResult:
    validate_tag_for_assignment(tag, tenant_id, application_id)
    try:
        assignment, created = TagAssignment.objects.get_or_create(
            tenant_id=tenant_id,
            application_id=application_id,
            resource_type=resource_type,
            resource_id=resource_id,
            tag=tag,
            defaults={"assigned_by": assigned_by},
        )
    except IntegrityError:
        assignment = TagAssignment.objects.get(
            tenant_id=tenant_id,
            application_id=application_id,
            resource_type=resource_type,
            resource_id=resource_id,
            tag=tag,
        )
        created = False
    return AssignmentResult(assignment=assignment, created=created)


def remove_tag_assignment(
    tenant_id: str,
    application_id: str,
    tag_id,
    resource_type: str,
    resource_id: str,
) -> int:
    deleted, _ = TagAssignment.objects.filter(
        tenant_id=tenant_id,
        application_id=application_id,
        tag_id=tag_id,
        resource_type=resource_type,
        resource_id=resource_id,
    ).delete()
    return deleted


def bulk_assign_tags(
    tenant_id: str,
    application_id: str,
    resource_type: str,
    resource_id: str,
    tag_ids: list,
    assigned_by: str | None = None,
) -> dict:
    tags = get_assignable_tags(tenant_id, application_id, tag_ids)
    created = 0
    existing = 0
    assignments = []

    with transaction.atomic():
        for tag in tags:
            result = assign_tag(
                tenant_id=tenant_id,
                application_id=application_id,
                tag=tag,
                resource_type=resource_type,
                resource_id=resource_id,
                assigned_by=assigned_by,
            )
            created += int(result.created)
            existing += int(not result.created)
            assignments.append(result.assignment)

    return {"created": created, "existing": existing, "skipped": 0, "assignments": assignments}


def bulk_remove_tags(
    tenant_id: str,
    application_id: str,
    resource_type: str,
    resource_id: str,
    tag_ids: list,
) -> int:
    max_bulk = getattr(settings, "MAX_BULK_TAGS", 200)
    if len(tag_ids) > max_bulk:
        raise serializers.ValidationError({"tag_ids": [f"Maximum bulk size is {max_bulk}."]})
    deleted, _ = TagAssignment.objects.filter(
        tenant_id=tenant_id,
        application_id=application_id,
        resource_type=resource_type,
        resource_id=resource_id,
        tag_id__in=tag_ids,
    ).delete()
    return deleted


def replace_resource_tags(
    tenant_id: str,
    application_id: str,
    resource_type: str,
    resource_id: str,
    tag_ids: list,
    assigned_by: str | None = None,
) -> dict:
    tags = get_assignable_tags(tenant_id, application_id, tag_ids)
    requested_ids = {tag.id for tag in tags}

    with transaction.atomic():
        existing = TagAssignment.objects.filter(
            tenant_id=tenant_id,
            application_id=application_id,
            resource_type=resource_type,
            resource_id=resource_id,
        )
        removed, _ = existing.exclude(tag_id__in=requested_ids).delete()
        remaining_ids = set(existing.values_list("tag_id", flat=True))

        created = 0
        for tag in tags:
            if tag.id in remaining_ids:
                continue
            assign_tag(tenant_id, application_id, tag, resource_type, resource_id, assigned_by)
            created += 1

        final_assignments = list(
            TagAssignment.objects.filter(
                tenant_id=tenant_id,
                application_id=application_id,
                resource_type=resource_type,
                resource_id=resource_id,
            ).select_related("tag")
        )

    return {"created": created, "removed": removed, "assignments": final_assignments}
