"""Service-backed taxonomy admin workflows (#85).

These drive the admin through real HTTP POSTs (session-authenticated superuser) and
assert the write went through the domain services — i.e. the audit log and outbox event
exist and are attributed to ``admin:<username>`` — and that a rejection the service
would raise never persists a primary row, audit row, or outbox event.
"""

from __future__ import annotations

import pytest
from django.test import override_settings

from octonomy.audit.models import AuditLog
from octonomy.events.models import OutboxEvent
from octonomy.tags.models import Tag, TagAlias, Vocabulary
from tests.admin.conftest import admin_enabled
from tests.factories import make_alias, make_tag, make_vocabulary

pytestmark = pytest.mark.django_db

TAG_ADD = "/admin/tags/tag/add/"
TAG_CHANGELIST = "/admin/tags/tag/"
VOCAB_ADD = "/admin/tags/vocabulary/add/"
ALIAS_ADD = "/admin/tags/tagalias/add/"


def _post(client, url, data, *, follow=True):
    with admin_enabled(True):
        return client.post(url, {"_save": "Save", **data}, follow=follow)


def _change_url(model_label, obj) -> str:
    return f"/admin/{model_label}/{obj.pk}/change/"


def _audit(**filters):
    return AuditLog.objects.filter(**filters)


def _outbox(**filters):
    return OutboxEvent.objects.filter(**filters)


def _tag_payload(**overrides) -> dict:
    data = {
        "tenant_id": "tenant_a",
        "application_id": "",
        "namespace_type": "",
        "namespace_id": "",
        "name": "Featured",
        "slug": "featured",
        "type": "label",
        "description": "",
        "parent": "",
        "vocabulary": "",
        "metadata": "{}",
    }
    data.update(overrides)
    return data


# --------------------------------------------------------------------------------
# Create through the admin, verifying audit + outbox side effects
# --------------------------------------------------------------------------------


def test_create_global_tag_records_admin_attributed_audit_and_outbox(client, superuser):
    client.force_login(superuser)
    _post(client, TAG_ADD, _tag_payload())

    tag = Tag.objects.get(slug="featured")
    assert tag.tenant_id == "tenant_a"
    assert tag.application_id is None
    assert tag.namespace_type is None and tag.namespace_id is None

    audit = _audit(entity_type="tag", entity_id=str(tag.id), action="tag.created").get()
    outbox = _outbox(aggregate_type="tag", aggregate_id=str(tag.id), event_type="tag.created").get()
    assert audit.actor_id == "admin:root"
    assert outbox.actor_id == "admin:root"
    assert audit.request_id  # stamped by RequestContextMiddleware, like a REST write
    # One operation_id per form submission, shared by the audit + outbox rows.
    assert audit.operation_id == outbox.operation_id


def test_create_application_scoped_vocabulary_through_admin(client, superuser):
    client.force_login(superuser)
    _post(
        client,
        VOCAB_ADD,
        {
            "tenant_id": "tenant_a",
            "application_id": "commerce",
            "namespace_type": "",
            "namespace_id": "",
            "name": "Labels",
            "slug": "labels",
            "description": "",
            "metadata": "{}",
        },
    )
    vocab = Vocabulary.objects.get(slug="labels")
    assert vocab.application_id == "commerce"
    assert vocab.namespace_type is None
    assert _audit(
        entity_type="vocabulary", entity_id=str(vocab.id), action="vocabulary.created"
    ).exists()


@override_settings(NAMESPACE_WRITE_ENABLED=True)
def test_create_namespaced_tag_through_admin(client, superuser):
    client.force_login(superuser)
    _post(
        client,
        TAG_ADD,
        _tag_payload(
            application_id="commerce",
            namespace_type="merchant",
            namespace_id="merchant_a",
            slug="promo",
            name="Promo",
        ),
    )
    tag = Tag.objects.get(slug="promo")
    assert tag.application_id == "commerce"
    assert (tag.namespace_type, tag.namespace_id) == ("merchant", "merchant_a")
    audit = _audit(entity_type="tag", entity_id=str(tag.id)).get()
    assert (audit.namespace_type, audit.namespace_id) == ("merchant", "merchant_a")


def test_create_alias_through_admin(client, superuser):
    tag = make_tag(slug="canonical")
    client.force_login(superuser)
    _post(
        client,
        ALIAS_ADD,
        {
            "tenant_id": "tenant_a",
            "application_id": "",
            "namespace_type": "",
            "namespace_id": "",
            "tag": str(tag.id),
            "name": "Promoted",
            "slug": "promoted",
            "metadata": "{}",
        },
    )
    alias = TagAlias.objects.get(slug="promoted")
    assert alias.tag_id == tag.id
    assert _audit(
        entity_type="tag_alias", entity_id=str(alias.id), action="tag_alias.created"
    ).exists()


# --------------------------------------------------------------------------------
# Shared-validator input checks (slug/type/metadata) surface as form errors
# --------------------------------------------------------------------------------


def test_invalid_slug_is_rejected_by_shared_validator(client, superuser):
    client.force_login(superuser)
    resp = _post(client, TAG_ADD, _tag_payload(slug="Not A Slug"))
    assert not Tag.objects.filter(name="Featured").exists()
    assert b"lowercase letters" in resp.content


def test_metadata_must_be_json_object(client, superuser):
    client.force_login(superuser)
    resp = _post(client, TAG_ADD, _tag_payload(metadata="[1, 2, 3]"))
    assert not Tag.objects.filter(slug="featured").exists()
    assert b"JSON object" in resp.content


# --------------------------------------------------------------------------------
# Cross-scope relationships are rejected with no side effects
# --------------------------------------------------------------------------------


def test_cross_tenant_parent_rejected_with_no_side_effects(client, superuser):
    other_parent = make_tag(tenant_id="tenant_b", slug="foreign-parent")
    client.force_login(superuser)
    resp = _post(client, TAG_ADD, _tag_payload(slug="child", parent=str(other_parent.id)))

    assert not Tag.objects.filter(slug="child").exists()
    # No audit/outbox for the rejected child were written.
    assert _audit(action="tag.created").count() == 0
    assert _outbox(event_type="tag.created").count() == 0
    assert b"same tenant" in resp.content


def test_shared_tag_cannot_use_app_specific_parent(client, superuser):
    app_parent = make_tag(application_id="commerce", slug="app-parent")
    client.force_login(superuser)
    resp = _post(client, TAG_ADD, _tag_payload(slug="shared-child", parent=str(app_parent.id)))

    assert not Tag.objects.filter(slug="shared-child").exists()
    assert b"shared parent" in resp.content.lower() or b"Parent must also be shared" in resp.content


# --------------------------------------------------------------------------------
# Scope immutability
# --------------------------------------------------------------------------------


def test_scope_fields_are_readonly_on_change(client, superuser):
    tag = make_tag(application_id="commerce", slug="fixed")
    client.force_login(superuser)
    with admin_enabled(True):
        page = client.get(_change_url("tags/tag", tag))
    assert page.status_code == 200
    # Scope fields are rendered read-only: no editable input/select is emitted for them.
    assert b'name="application_id"' not in page.content
    assert b'name="namespace_type"' not in page.content


def test_scope_change_attempt_on_change_form_is_ignored(client, superuser):
    tag = make_tag(application_id="commerce", slug="fixed")
    client.force_login(superuser)
    _post(
        client,
        _change_url("tags/tag", tag),
        {
            "name": "Renamed",
            "slug": "fixed",
            "type": tag.type,
            "description": "",
            "parent": "",
            "vocabulary": "",
            "metadata": "{}",
            # Attempt to move scope — must be ignored (field is read-only).
            "application_id": "other-app",
        },
    )
    tag.refresh_from_db()
    assert tag.application_id == "commerce"  # unchanged
    assert tag.name == "Renamed"  # the editable field did change


# --------------------------------------------------------------------------------
# Kill-switch
# --------------------------------------------------------------------------------


@override_settings(NAMESPACE_WRITE_ENABLED=False)
def test_namespaced_write_rejected_when_kill_switch_off(client, superuser):
    client.force_login(superuser)
    resp = _post(
        client,
        TAG_ADD,
        _tag_payload(
            application_id="commerce",
            namespace_type="merchant",
            namespace_id="merchant_a",
            slug="promo",
        ),
    )
    assert not Tag.objects.filter(slug="promo").exists()
    assert _outbox(event_type="tag.created").count() == 0
    assert b"Namespaced writes are" in resp.content or b"disabled" in resp.content.lower()


# --------------------------------------------------------------------------------
# Duplicate active slug + conflicting reactivation
# --------------------------------------------------------------------------------


def test_duplicate_active_slug_is_user_visible_error(client, superuser):
    make_tag(slug="featured", type="label")
    client.force_login(superuser)
    _post(client, TAG_ADD, _tag_payload(slug="featured", type="label"))
    # No second active row was created.
    assert Tag.objects.filter(slug="featured", type="label", is_active=True).count() == 1


def _run_changelist_action(client, changelist, action, ids):
    data = {
        "action": action,
        "index": "0",
        "select_across": "0",
        "_selected_action": [str(pk) for pk in ids],
    }
    with admin_enabled(True):
        return client.post(changelist, data, follow=True)


def test_reactivation_into_active_slug_conflict_is_reported(client, superuser):
    tag = make_tag(slug="featured", type="label", is_active=False)
    make_tag(slug="featured", type="label", is_active=True)  # occupies the active slug
    client.force_login(superuser)

    resp = _run_changelist_action(client, TAG_CHANGELIST, "reactivate_selected", [tag.id])
    tag.refresh_from_db()
    assert tag.is_active is False  # conflict blocked the reactivation
    assert b"No rows were reactivated" in resp.content


# --------------------------------------------------------------------------------
# Deactivate cascade + no hard delete
# --------------------------------------------------------------------------------


def test_deactivate_tag_cascades_active_aliases_and_soft_deletes(client, superuser):
    tag = make_tag(slug="canonical")
    alias = make_alias(tag=tag, slug="alt")
    client.force_login(superuser)

    _run_changelist_action(client, TAG_CHANGELIST, "deactivate_selected", [tag.id])

    tag.refresh_from_db()
    alias.refresh_from_db()
    assert tag.is_active is False  # soft-deleted, not removed
    assert Tag.objects.filter(pk=tag.pk).exists()
    assert alias.is_active is False  # cascaded
    # The cascade stays auditable: the tag's deactivation audit names the cascaded
    # aliases, and each cascaded alias emits its own outbox event.
    tag_audit = _audit(action="tag.deactivated", entity_id=str(tag.id)).get()
    assert str(alias.id) in tag_audit.changes.get("cascaded_alias_ids", [])
    assert _outbox(event_type="tag_alias.deactivated", aggregate_id=str(alias.id)).exists()


class _FakeRequest:
    def __init__(self, user):
        self.user = user
        self.GET = {}


def test_taxonomy_admin_has_no_hard_delete_permission(client, superuser):
    from django.contrib import admin as django_admin

    tag_admin = django_admin.site._registry[Tag]
    assert tag_admin.has_delete_permission(_FakeRequest(superuser)) is False
    assert "delete_selected" not in tag_admin.get_actions(_FakeRequest(superuser))


def _admin_request(rf, superuser):
    request = rf.post("/admin/tags/tag/")
    request.user = superuser
    request.request_id = "req_admin_test"  # RequestFactory skips middleware
    return request


def test_delete_model_routes_to_soft_deactivate(rf, superuser):
    from django.contrib import admin as django_admin

    tag = make_tag(slug="canonical")
    tag_admin = django_admin.site._registry[Tag]
    tag_admin.delete_model(_admin_request(rf, superuser), tag)

    tag.refresh_from_db()
    assert tag.is_active is False  # soft-deactivated, never hard-deleted
    assert Tag.objects.filter(pk=tag.pk).exists()
    assert _audit(action="tag.deactivated", entity_id=str(tag.id)).exists()


def test_delete_queryset_routes_to_soft_deactivate(rf, superuser):
    from django.contrib import admin as django_admin

    a = make_tag(slug="aa", type="label")
    b = make_tag(slug="bb", type="label")
    tag_admin = django_admin.site._registry[Tag]
    tag_admin.delete_queryset(
        _admin_request(rf, superuser), Tag.objects.filter(pk__in=[a.pk, b.pk])
    )

    a.refresh_from_db()
    b.refresh_from_db()
    assert a.is_active is False and b.is_active is False
    assert Tag.objects.filter(pk__in=[a.pk, b.pk]).count() == 2  # both still present


# --------------------------------------------------------------------------------
# Atomic bulk actions
# --------------------------------------------------------------------------------


def test_bulk_deactivate_action_soft_deletes(client, superuser):
    tag = make_tag(slug="canonical")
    client.force_login(superuser)
    _run_changelist_action(client, TAG_CHANGELIST, "deactivate_selected", [tag.id])
    tag.refresh_from_db()
    assert tag.is_active is False
    assert _audit(action="tag.deactivated", entity_id=str(tag.id)).exists()


def test_bulk_reactivate_action_revives_row(client, superuser):
    tag = make_tag(slug="canonical", is_active=False)
    client.force_login(superuser)
    _run_changelist_action(client, TAG_CHANGELIST, "reactivate_selected", [tag.id])
    tag.refresh_from_db()
    assert tag.is_active is True
    assert _audit(action="tag.updated", entity_id=str(tag.id)).exists()


def test_reactivate_blocked_when_vocabulary_inactive(client, superuser):
    # Active-relation guard: reviving a tag whose vocabulary is now inactive is rejected.
    # The guard lives in reactivate_tag (service), which re-checks under a row lock.
    vocab = make_vocabulary(slug="labels", is_active=False)
    tag = make_tag(slug="scoped", is_active=False, vocabulary=vocab)
    client.force_login(superuser)
    _run_changelist_action(client, TAG_CHANGELIST, "reactivate_selected", [tag.id])
    tag.refresh_from_db()
    assert tag.is_active is False  # guard blocked the reactivation


def test_bulk_reactivate_is_atomic_on_conflict(client, superuser):
    a = make_tag(slug="aa", type="label", is_active=False)
    b = make_tag(slug="bb", type="label", is_active=False)
    make_tag(slug="aa", type="label", is_active=True)  # makes reactivating `a` conflict
    client.force_login(superuser)

    _run_changelist_action(client, TAG_CHANGELIST, "reactivate_selected", [a.id, b.id])

    a.refresh_from_db()
    b.refresh_from_db()
    # One row in the selection conflicts, so the WHOLE selection rolls back.
    assert a.is_active is False
    assert b.is_active is False
