from __future__ import annotations

from django.db import IntegrityError, transaction
from rest_framework import serializers

from octonomy.audit.services import create_audit_log, tag_alias_snapshot
from octonomy.core.audit import AuditContext
from octonomy.core.auth import GLOBAL_SCOPE, ScopeContext, guard_namespace_write_enabled
from octonomy.core.errors import ApplicationMismatchError, ConflictError, DomainError
from octonomy.core.metrics import emit_namespace_conflict
from octonomy.core.selectors import (
    namespace_fields,
    row_matches_scope,
    scope_context_from_values,
)
from octonomy.events.services import create_outbox_event
from octonomy.tags.alias_selectors import (
    active_aliases_for_resolution,
    active_tags_for_resolution,
)
from octonomy.tags.models import Tag, TagAlias
from octonomy.tags.services import validate_metadata


def validate_alias_tag(
    tenant_id: str,
    application_id: str | None,
    tag: Tag,
    scope_context: ScopeContext = GLOBAL_SCOPE,
) -> None:
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

    # App-specific canonical tags cannot be given shared aliases or aliases in a
    # different application; otherwise alias assignment could bypass the tag
    # application boundary.
    if tag.application_id is not None and application_id != tag.application_id:
        raise ApplicationMismatchError(
            "App-specific tags can only use aliases in the same application.",
            {"application_id": ["Alias application is incompatible with tag."]},
        )

    if not row_matches_scope(tag, scope_context, include_global=True):
        if scope_context.is_global:
            raise DomainError(
                "Global aliases can only target global tags.",
                {"tag_id": ["Tag namespace is incompatible with alias."]},
            )
        raise DomainError(
            "Namespaced aliases can only target global or same-namespace tags.",
            {"tag_id": ["Tag namespace is incompatible with alias."]},
        )


def alias_scope_context(data: dict) -> ScopeContext:
    return scope_context_from_values(data.get("namespace_type"), data.get("namespace_id"))


def effective_resolution_scope(
    scope_context: ScopeContext,
    scope_qualifier: str | None,
    authorized_global: bool = True,
) -> tuple[ScopeContext, bool]:
    # ``authorized_global`` is the caller's fail-closed opt-in for global rows
    # (``request_include_global`` at the view). A merchant request that is not
    # authorized for global must not reach global tags/aliases here, whether via
    # the default global fallback or an explicit ``scope=global`` pin — otherwise
    # tag-resolution becomes a discovery side channel around the exclude-default
    # contract.
    if scope_qualifier is None:
        return scope_context, authorized_global
    if scope_qualifier == "global":
        if not authorized_global:
            # Indistinguishable from a genuine no-match: no existence disclosure.
            raise serializers.ValidationError(
                {"slug": ["No active tag or alias matched this slug."]}
            )
        return GLOBAL_SCOPE, False
    if scope_qualifier == "merchant":
        if scope_context.is_global:
            raise serializers.ValidationError(
                {"scope": ["Merchant scope requires a namespaced request."]}
            )
        return scope_context, False
    raise serializers.ValidationError({"scope": ["Use 'global' or 'merchant'."]})


def create_tag_alias(
    tenant_id: str,
    data: dict,
    audit_context: AuditContext | None = None,
) -> TagAlias:
    data["tenant_id"] = tenant_id
    validate_metadata(data.get("metadata", {}))
    scope_context = alias_scope_context(data)
    guard_namespace_write_enabled(scope_context)
    with transaction.atomic():
        data["tag"] = Tag.objects.select_for_update().get(id=data["tag"].id)
        validate_alias_tag(
            tenant_id,
            data.get("application_id"),
            data["tag"],
            scope_context,
        )
        # Only the alias write is translated to a duplicate-slug 409 (and metric);
        # audit/outbox integrity errors propagate untouched (see create_tag).
        try:
            with transaction.atomic():
                alias = TagAlias.objects.create(**data)
        except IntegrityError:
            emit_namespace_conflict("tag_alias", scope_context)
            raise ConflictError(
                "An active tag alias with this tenant, application, and slug already exists.",
                {"slug": ["Duplicate active alias slug."]},
            )
        create_audit_log(
            tenant_id=tenant_id,
            application_id=alias.application_id,
            **namespace_fields(alias),
            action="tag_alias.created",
            entity_type="tag_alias",
            entity_id=str(alias.id),
            tag_id=alias.tag_id,
            audit_context=audit_context,
            changes={"after": tag_alias_snapshot(alias)},
        )
        create_outbox_event(
            tenant_id=tenant_id,
            application_id=alias.application_id,
            **namespace_fields(alias),
            event_type="tag_alias.created",
            aggregate_type="tag_alias",
            aggregate_id=str(alias.id),
            tag_id=alias.tag_id,
            audit_context=audit_context,
            payload={"after": tag_alias_snapshot(alias)},
        )
        return alias


def update_tag_alias(
    alias: TagAlias,
    data: dict,
    audit_context: AuditContext | None = None,
) -> TagAlias:
    application_id = data.get("application_id", alias.application_id)
    tag = data.get("tag", alias.tag)
    scope_context = ScopeContext(
        namespace_type=data.get("namespace_type", alias.namespace_type),
        namespace_id=data.get("namespace_id", alias.namespace_id),
    )
    # Guard the current scope as well as the destination so a namespaced->global
    # move cannot slip past the kill-switch (see update_tag).
    guard_namespace_write_enabled(
        scope_context_from_values(alias.namespace_type, alias.namespace_id)
    )
    guard_namespace_write_enabled(scope_context)
    validate_alias_tag(alias.tenant_id, application_id, tag, scope_context)
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

    with transaction.atomic():
        locked_tag = Tag.objects.select_for_update().get(id=tag.id)
        validate_alias_tag(alias.tenant_id, application_id, locked_tag, scope_context)
        alias.tag = locked_tag
        # Only the alias write is translated to a duplicate-slug 409 (and metric);
        # audit/outbox integrity errors propagate untouched (see create_tag).
        try:
            with transaction.atomic():
                alias.save()
        except IntegrityError:
            emit_namespace_conflict("tag_alias", scope_context)
            raise ConflictError(
                "An active tag alias with this tenant, application, and slug already exists.",
                {"slug": ["Duplicate active alias slug."]},
            )
        create_audit_log(
            tenant_id=alias.tenant_id,
            application_id=alias.application_id,
            **namespace_fields(alias),
            action="tag_alias.updated",
            entity_type="tag_alias",
            entity_id=str(alias.id),
            tag_id=alias.tag_id,
            audit_context=audit_context,
            changes={"before": changed_before, "after": changed_after},
        )
        create_outbox_event(
            tenant_id=alias.tenant_id,
            application_id=alias.application_id,
            **namespace_fields(alias),
            event_type="tag_alias.updated",
            aggregate_type="tag_alias",
            aggregate_id=str(alias.id),
            tag_id=alias.tag_id,
            audit_context=audit_context,
            payload={"before": changed_before, "after": changed_after},
        )
    return alias


def deactivate_tag_alias(alias: TagAlias, audit_context: AuditContext | None = None) -> bool:
    guard_namespace_write_enabled(
        scope_context_from_values(alias.namespace_type, alias.namespace_id)
    )
    if not alias.is_active:
        return False

    alias.is_active = False
    with transaction.atomic():
        alias.save(update_fields=["is_active", "updated_at"])
        create_audit_log(
            tenant_id=alias.tenant_id,
            application_id=alias.application_id,
            **namespace_fields(alias),
            action="tag_alias.deactivated",
            entity_type="tag_alias",
            entity_id=str(alias.id),
            tag_id=alias.tag_id,
            audit_context=audit_context,
            changes={"before": {"is_active": True}, "after": {"is_active": False}},
        )
        create_outbox_event(
            tenant_id=alias.tenant_id,
            application_id=alias.application_id,
            **namespace_fields(alias),
            event_type="tag_alias.deactivated",
            aggregate_type="tag_alias",
            aggregate_id=str(alias.id),
            tag_id=alias.tag_id,
            audit_context=audit_context,
            payload={"before": {"is_active": True}, "after": {"is_active": False}},
        )
    return True


def most_specific_matches(rows: list) -> list:
    first_priority = getattr(rows[0], "resolution_priority", None)
    if first_priority is None:
        return rows
    return [row for row in rows if getattr(row, "resolution_priority", None) == first_priority]


def resolve_tag_reference(
    tenant_id: str,
    slug: str,
    application_id: str | None,
    tag_type: str | None = None,
    scope_context: ScopeContext = GLOBAL_SCOPE,
    scope_qualifier: str | None = None,
    authorized_global: bool = True,
) -> dict:
    resolved_scope, include_global = effective_resolution_scope(
        scope_context, scope_qualifier, authorized_global
    )
    tags = list(
        active_tags_for_resolution(
            tenant_id,
            slug,
            application_id,
            tag_type,
            resolved_scope,
            include_global=include_global,
        )
    )
    if tags:
        # Canonical tags win over aliases for the same slug. Within an
        # application, prefer the app-specific canonical tag before falling back
        # to a shared tag so local vocabulary can override tenant-wide defaults.
        candidate_tags = tags
        if application_id:
            app_tags = [tag for tag in tags if tag.application_id == application_id]
            if app_tags:
                candidate_tags = app_tags
        candidate_tags = most_specific_matches(candidate_tags)
        if len(candidate_tags) > 1:
            raise serializers.ValidationError(
                {"type": ["Multiple canonical tags match this slug; provide type."]}
            )
        return {"matched_type": "tag", "matched_alias": None, "tag": candidate_tags[0]}

    alias = active_aliases_for_resolution(
        tenant_id,
        slug,
        application_id,
        resolved_scope,
        include_global=include_global,
    ).first()
    if alias is None:
        raise serializers.ValidationError({"slug": ["No active tag or alias matched this slug."]})

    return {"matched_type": "alias", "matched_alias": alias, "tag": alias.tag}


def resolve_assignable_alias(
    *,
    tenant_id: str,
    application_id: str,
    scope_context: ScopeContext = GLOBAL_SCOPE,
    include_global: bool = True,
    alias_id=None,
    alias_slug: str | None = None,
) -> Tag:
    field = "alias_id" if alias_id else "alias_slug"
    if alias_id:
        try:
            alias = (
                TagAlias.objects.for_tenant(tenant_id)
                .select_related("tag")
                .filter(id=alias_id)
                .get()
            )
        except TagAlias.DoesNotExist:
            raise serializers.ValidationError({"alias_id": ["Alias was not found."]})
        if not row_matches_scope(alias, scope_context, include_global=include_global):
            raise serializers.ValidationError({"alias_id": ["Alias was not found."]})
        if not row_matches_scope(alias.tag, scope_context, include_global=include_global):
            raise serializers.ValidationError({"alias_id": ["Alias was not found."]})
    else:
        alias = active_aliases_for_resolution(
            tenant_id,
            alias_slug,
            application_id,
            scope_context,
            include_global=include_global,
        ).first()
        if alias is None:
            raise serializers.ValidationError({"alias_slug": ["Alias was not found."]})

    if not alias.is_active:
        raise serializers.ValidationError({field: ["Inactive aliases cannot be assigned."]})

    if not alias.tag.is_active:
        raise serializers.ValidationError({field: ["Alias tag is inactive."]})

    # Alias assignment resolves to the canonical tag, but the alias scope still
    # matters. Without this check an app-specific alias could assign tags outside
    # the caller application.
    if alias.application_id is not None and alias.application_id != application_id:
        raise ApplicationMismatchError(
            details={"application_id": ["Alias belongs to another application."]}
        )

    return alias.tag
