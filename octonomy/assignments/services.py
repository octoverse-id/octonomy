from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings
from django.db import IntegrityError, transaction
from rest_framework import serializers

from octonomy.assignments.models import TagAssignment
from octonomy.audit.services import (
    assignment_snapshot,
    build_audit_log,
    create_audit_log,
    create_audit_logs,
)
from octonomy.core.audit import AuditContext
from octonomy.core.errors import ApplicationMismatchError, InactiveTagError
from octonomy.events.services import build_outbox_event, create_outbox_event, create_outbox_events
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
    audit_context: AuditContext | None = None,
) -> AssignmentResult:
    validate_tag_for_assignment(tag, tenant_id, application_id)
    try:
        with transaction.atomic():
            assignment, created = TagAssignment.objects.get_or_create(
                tenant_id=tenant_id,
                application_id=application_id,
                resource_type=resource_type,
                resource_id=resource_id,
                tag=tag,
                defaults={"assigned_by": assigned_by},
            )
            if created:
                create_audit_log(
                    tenant_id=tenant_id,
                    application_id=application_id,
                    action="assignment.created",
                    entity_type="tag_assignment",
                    entity_id=str(assignment.id),
                    tag_id=tag.id,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    audit_context=audit_context,
                    changes={"after": assignment_snapshot(assignment)},
                )
                create_outbox_event(
                    tenant_id=tenant_id,
                    application_id=application_id,
                    event_type="assignment.created",
                    aggregate_type="tag_assignment",
                    aggregate_id=str(assignment.id),
                    tag_id=tag.id,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    audit_context=audit_context,
                    payload={"after": assignment_snapshot(assignment)},
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
    audit_context: AuditContext | None = None,
) -> int:
    queryset = TagAssignment.objects.filter(
        tenant_id=tenant_id,
        application_id=application_id,
        tag_id=tag_id,
        resource_type=resource_type,
        resource_id=resource_id,
    )
    with transaction.atomic():
        assignments = list(queryset.select_for_update())
        if not assignments:
            return 0
        assignment_ids = [assignment.id for assignment in assignments]
        outbox_events = []
        for assignment in assignments:
            create_audit_log(
                tenant_id=tenant_id,
                application_id=application_id,
                action="assignment.removed",
                entity_type="tag_assignment",
                entity_id=str(assignment.id),
                tag_id=assignment.tag_id,
                resource_type=resource_type,
                resource_id=resource_id,
                audit_context=audit_context,
                changes={"before": assignment_snapshot(assignment)},
            )
            outbox_events.append(
                build_outbox_event(
                    tenant_id=tenant_id,
                    application_id=application_id,
                    event_type="assignment.removed",
                    aggregate_type="tag_assignment",
                    aggregate_id=str(assignment.id),
                    tag_id=assignment.tag_id,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    audit_context=audit_context,
                    payload={"before": assignment_snapshot(assignment)},
                )
            )
        create_outbox_events(outbox_events)
        deleted, _ = TagAssignment.objects.filter(id__in=assignment_ids).delete()
    return deleted


def bulk_assign_tags(
    tenant_id: str,
    application_id: str,
    resource_type: str,
    resource_id: str,
    tag_ids: list,
    assigned_by: str | None = None,
    audit_context: AuditContext | None = None,
) -> dict:
    tags = get_assignable_tags(tenant_id, application_id, tag_ids)
    created = 0
    existing = 0
    assignments = []

    with transaction.atomic():
        audit_logs = []
        outbox_events = []
        for tag in tags:
            assignment, was_created = TagAssignment.objects.get_or_create(
                tenant_id=tenant_id,
                application_id=application_id,
                resource_type=resource_type,
                resource_id=resource_id,
                tag=tag,
                defaults={"assigned_by": assigned_by},
            )
            created += int(was_created)
            existing += int(not was_created)
            assignments.append(assignment)

            if was_created:
                audit_log = build_audit_log(
                    tenant_id=tenant_id,
                    application_id=application_id,
                    action="assignment.created",
                    entity_type="tag_assignment",
                    entity_id=str(assignment.id),
                    tag_id=tag.id,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    audit_context=audit_context,
                    changes={"after": assignment_snapshot(assignment)},
                )
                if audit_log is not None:
                    audit_logs.append(audit_log)
                outbox_events.append(
                    build_outbox_event(
                        tenant_id=tenant_id,
                        application_id=application_id,
                        event_type="assignment.created",
                        aggregate_type="tag_assignment",
                        aggregate_id=str(assignment.id),
                        tag_id=tag.id,
                        resource_type=resource_type,
                        resource_id=resource_id,
                        audit_context=audit_context,
                        payload={"after": assignment_snapshot(assignment)},
                    )
                )

        create_audit_logs(audit_logs)
        create_outbox_events(outbox_events)

    return {"created": created, "existing": existing, "skipped": 0, "assignments": assignments}


def bulk_remove_tags(
    tenant_id: str,
    application_id: str,
    resource_type: str,
    resource_id: str,
    tag_ids: list,
    audit_context: AuditContext | None = None,
) -> int:
    max_bulk = getattr(settings, "MAX_BULK_TAGS", 200)
    if len(tag_ids) > max_bulk:
        raise serializers.ValidationError({"tag_ids": [f"Maximum bulk size is {max_bulk}."]})
    queryset = TagAssignment.objects.filter(
        tenant_id=tenant_id,
        application_id=application_id,
        resource_type=resource_type,
        resource_id=resource_id,
        tag_id__in=tag_ids,
    )

    with transaction.atomic():
        assignments = list(queryset.select_for_update())
        if not assignments:
            return 0
        audit_logs = []
        outbox_events = []
        for assignment in assignments:
            audit_log = build_audit_log(
                tenant_id=tenant_id,
                application_id=application_id,
                action="assignment.removed",
                entity_type="tag_assignment",
                entity_id=str(assignment.id),
                tag_id=assignment.tag_id,
                resource_type=resource_type,
                resource_id=resource_id,
                audit_context=audit_context,
                changes={"before": assignment_snapshot(assignment)},
            )
            if audit_log is not None:
                audit_logs.append(audit_log)
            outbox_events.append(
                build_outbox_event(
                    tenant_id=tenant_id,
                    application_id=application_id,
                    event_type="assignment.removed",
                    aggregate_type="tag_assignment",
                    aggregate_id=str(assignment.id),
                    tag_id=assignment.tag_id,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    audit_context=audit_context,
                    payload={"before": assignment_snapshot(assignment)},
                )
            )
        create_audit_logs(audit_logs)
        create_outbox_events(outbox_events)
        deleted, _ = TagAssignment.objects.filter(
            id__in=[assignment.id for assignment in assignments]
        ).delete()
    return deleted


def replace_resource_tags(
    tenant_id: str,
    application_id: str,
    resource_type: str,
    resource_id: str,
    tag_ids: list,
    assigned_by: str | None = None,
    audit_context: AuditContext | None = None,
) -> dict:
    tags = get_assignable_tags(tenant_id, application_id, tag_ids)
    requested_ids = {tag.id for tag in tags}

    with transaction.atomic():
        existing = TagAssignment.objects.filter(
            tenant_id=tenant_id,
            application_id=application_id,
            resource_type=resource_type,
            resource_id=resource_id,
        ).select_for_update()
        removed_assignments = list(existing.exclude(tag_id__in=requested_ids))
        removed_ids = [assignment.id for assignment in removed_assignments]
        audit_logs = []
        outbox_events = []
        for assignment in removed_assignments:
            audit_log = build_audit_log(
                tenant_id=tenant_id,
                application_id=application_id,
                action="assignment.removed",
                entity_type="tag_assignment",
                entity_id=str(assignment.id),
                tag_id=assignment.tag_id,
                resource_type=resource_type,
                resource_id=resource_id,
                audit_context=audit_context,
                changes={"before": assignment_snapshot(assignment)},
            )
            if audit_log is not None:
                audit_logs.append(audit_log)
            outbox_events.append(
                build_outbox_event(
                    tenant_id=tenant_id,
                    application_id=application_id,
                    event_type="assignment.removed",
                    aggregate_type="tag_assignment",
                    aggregate_id=str(assignment.id),
                    tag_id=assignment.tag_id,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    audit_context=audit_context,
                    payload={"before": assignment_snapshot(assignment)},
                )
            )
        create_audit_logs(audit_logs)
        create_outbox_events(outbox_events)
        removed, _ = TagAssignment.objects.filter(id__in=removed_ids).delete()
        remaining_ids = set(existing.values_list("tag_id", flat=True))

        created = 0
        for tag in tags:
            if tag.id in remaining_ids:
                continue
            assign_tag(
                tenant_id,
                application_id,
                tag,
                resource_type,
                resource_id,
                assigned_by,
                audit_context=audit_context,
            )
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
