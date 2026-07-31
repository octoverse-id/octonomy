"""Service-backed TagAssignment admin workflows (#85).

Assignments are created idempotently through ``assign_tag`` and removed through
``remove_tag_assignment`` (a service-backed hard delete that still emits audit +
outbox). These run against PostgreSQL (the test ``DATABASE_URL``), exercising the real
unique constraints behind idempotency rather than mocked storage.
"""

from __future__ import annotations

import pytest
from django.test import override_settings

from octonomy.assignments.models import TagAssignment
from octonomy.audit.models import AuditLog
from octonomy.events.models import OutboxEvent
from tests.admin.conftest import admin_enabled
from tests.factories import make_tag

pytestmark = pytest.mark.django_db

ASSIGN_ADD = "/admin/assignments/tagassignment/add/"
ASSIGN_CHANGELIST = "/admin/assignments/tagassignment/"


def _post(client, url, data, *, follow=True):
    with admin_enabled(True):
        return client.post(url, {"_save": "Save", **data}, follow=follow)


def _run_action(client, action, ids):
    data = {
        "action": action,
        "index": "0",
        "select_across": "0",
        "_selected_action": [str(pk) for pk in ids],
    }
    with admin_enabled(True):
        return client.post(ASSIGN_CHANGELIST, data, follow=True)


def _assign_payload(tag, **overrides) -> dict:
    data = {
        "tenant_id": "tenant_a",
        "application_id": "commerce",
        "namespace_type": "",
        "namespace_id": "",
        "tag": str(tag.id),
        "resource_type": "product",
        "resource_id": "sku-1",
        "assigned_by": "",
    }
    data.update(overrides)
    return data


def test_assign_tag_through_admin_creates_assignment_and_audit(client, superuser):
    tag = make_tag(slug="canonical")
    client.force_login(superuser)
    _post(client, ASSIGN_ADD, _assign_payload(tag))

    assignment = TagAssignment.objects.get(resource_id="sku-1")
    assert assignment.tag_id == tag.id
    audit = AuditLog.objects.get(entity_type="tag_assignment", action="assignment.created")
    assert audit.actor_id == "admin:root"
    assert OutboxEvent.objects.filter(
        aggregate_type="tag_assignment", event_type="assignment.created"
    ).exists()


def test_repeat_assignment_is_idempotent(client, superuser):
    tag = make_tag(slug="canonical")
    client.force_login(superuser)
    _post(client, ASSIGN_ADD, _assign_payload(tag))
    _post(client, ASSIGN_ADD, _assign_payload(tag))

    assert TagAssignment.objects.filter(resource_id="sku-1", tag=tag).count() == 1
    assert (
        AuditLog.objects.filter(entity_type="tag_assignment", action="assignment.created").count()
        == 1
    )


def test_inactive_tag_assignment_is_rejected(client, superuser):
    tag = make_tag(slug="disabled", is_active=False)
    client.force_login(superuser)
    resp = _post(client, ASSIGN_ADD, _assign_payload(tag))

    assert not TagAssignment.objects.filter(resource_id="sku-1").exists()
    assert b"Inactive tags cannot be assigned" in resp.content


def test_remove_assignment_through_action_emits_audit(client, superuser):
    tag = make_tag(slug="canonical")
    assignment = TagAssignment.objects.create(
        tenant_id="tenant_a",
        application_id="commerce",
        tag=tag,
        resource_type="product",
        resource_id="sku-1",
    )
    client.force_login(superuser)

    _run_action(client, "remove_selected", [assignment.id])

    assert not TagAssignment.objects.filter(pk=assignment.pk).exists()
    audit = AuditLog.objects.get(entity_type="tag_assignment", action="assignment.removed")
    assert audit.actor_id == "admin:root"
    assert OutboxEvent.objects.filter(
        aggregate_type="tag_assignment", event_type="assignment.removed"
    ).exists()


def test_assignment_admin_denies_change_and_delete(client, superuser):
    from django.contrib import admin as django_admin

    assignment_admin = django_admin.site._registry[TagAssignment]

    class _Req:
        def __init__(self, user):
            self.user = user
            self.GET = {}

    request = _Req(superuser)
    assert assignment_admin.has_change_permission(request) is False
    assert assignment_admin.has_delete_permission(request) is False
    assert "delete_selected" not in assignment_admin.get_actions(request)


@override_settings(NAMESPACE_WRITE_ENABLED=True)
def test_namespaced_assignment_idempotent_on_postgres(client, superuser):
    tag = make_tag(
        application_id="commerce",
        namespace_type="merchant",
        namespace_id="merchant_a",
        slug="ns-tag",
    )
    client.force_login(superuser)
    payload = _assign_payload(
        tag,
        namespace_type="merchant",
        namespace_id="merchant_a",
        resource_id="sku-ns",
    )
    _post(client, ASSIGN_ADD, payload)
    _post(client, ASSIGN_ADD, payload)

    assignments = TagAssignment.objects.filter(resource_id="sku-ns", tag=tag)
    assert assignments.count() == 1
    assert assignments.first().namespace_id == "merchant_a"
