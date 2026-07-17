"""The write kill-switch is enforced below HTTP (issue #45).

``NAMESPACE_WRITE_ENABLED`` must gate every write path, not just HTTP routing.
These tests call the domain service directly — the same entry point a management
command or background writer uses — and assert a namespaced write is refused when
the flag is off, while global writes and (flag-on) namespaced writes succeed.
"""

from __future__ import annotations

import pytest
from django.test import override_settings
from rest_framework import serializers

from octonomy.assignments.services import assign_tag, get_or_create_assignment
from octonomy.core.auth import GLOBAL_SCOPE, ScopeContext
from octonomy.core.errors import NamespacedWritesDisabledError
from octonomy.tags.services import create_tag, deactivate_tag, update_tag
from tests.factories import make_alias, make_tag

pytestmark = pytest.mark.django_db

MERCHANT_A = ScopeContext("merchant", "merchant_a")


def _tag_data(**overrides):
    data = {
        "application_id": "commerce",
        "slug": "premium",
        "type": "label",
        "name": "Premium",
        "metadata": {},
    }
    data.update(overrides)
    return data


@override_settings(NAMESPACE_WRITE_ENABLED=False)
def test_namespaced_service_write_is_refused_when_kill_switch_off():
    with pytest.raises(NamespacedWritesDisabledError):
        create_tag(
            "tenant_a",
            _tag_data(namespace_type="merchant", namespace_id="merchant_a"),
        )


@override_settings(NAMESPACE_WRITE_ENABLED=False)
def test_global_service_write_is_allowed_when_kill_switch_off():
    tag = create_tag("tenant_a", _tag_data())
    assert tag.namespace_type is None
    assert tag.namespace_id is None


@override_settings(NAMESPACE_WRITE_ENABLED=True)
def test_namespaced_service_write_is_allowed_when_kill_switch_on():
    tag = create_tag(
        "tenant_a",
        _tag_data(namespace_type="merchant", namespace_id="merchant_a"),
    )
    assert tag.namespace_type == "merchant"
    assert tag.namespace_id == "merchant_a"


@override_settings(NAMESPACE_WRITE_ENABLED=False)
def test_namespaced_to_global_move_is_refused_when_kill_switch_off():
    # A namespaced->global move mutates a namespaced row (and would expose it to
    # global reads); the destination scope is global, so the guard must also inspect
    # the row's current scope or the move slips through.
    tag = make_tag(
        application_id="commerce",
        namespace_type="merchant",
        namespace_id="merchant_a",
        slug="movable",
    )
    with pytest.raises(NamespacedWritesDisabledError):
        update_tag(
            tag, {"namespace_type": None, "namespace_id": None, "application_id": "commerce"}
        )


@override_settings(NAMESPACE_WRITE_ENABLED=False)
def test_namespaced_assignment_helper_is_refused_when_kill_switch_off():
    # get_or_create_assignment is importable; a direct caller must still be gated.
    tag = make_tag(
        application_id="commerce",
        namespace_type="merchant",
        namespace_id="merchant_a",
        slug="assignable",
    )
    with pytest.raises(NamespacedWritesDisabledError):
        get_or_create_assignment(
            tenant_id="tenant_a",
            application_id="commerce",
            scope_context=MERCHANT_A,
            resource_type="product",
            resource_id="p1",
            tag=tag,
        )
    with pytest.raises(NamespacedWritesDisabledError):
        assign_tag(
            tenant_id="tenant_a",
            application_id="commerce",
            tag=tag,
            resource_type="product",
            resource_id="p1",
            scope_context=MERCHANT_A,
        )


@override_settings(NAMESPACE_WRITE_ENABLED=False)
def test_global_assignment_is_allowed_when_kill_switch_off():
    tag = make_tag(application_id="commerce", slug="global-assignable")
    _assignment, created = get_or_create_assignment(
        tenant_id="tenant_a",
        application_id="commerce",
        scope_context=GLOBAL_SCOPE,
        resource_type="product",
        resource_id="p1",
        tag=tag,
    )
    assert created is True


@override_settings(NAMESPACE_WRITE_ENABLED=False)
def test_global_tag_deactivation_is_refused_when_it_cascades_to_a_namespaced_alias():
    # Deactivating a global tag cascades to deactivate every active alias pointing at
    # it — including merchant aliases. That is a namespaced write, so the kill-switch
    # must reject the whole (atomic) deactivation, not just guard the tag's own scope.
    global_tag = make_tag(application_id="commerce", slug="global-canonical")
    make_alias(
        tag=global_tag,
        application_id="commerce",
        namespace_type="merchant",
        namespace_id="merchant_a",
        slug="merchant-alias",
    )
    with pytest.raises(NamespacedWritesDisabledError):
        deactivate_tag(global_tag)
    global_tag.refresh_from_db()
    assert global_tag.is_active is True  # rolled back, not left half-deactivated


@override_settings(NAMESPACE_WRITE_ENABLED=False)
def test_global_tag_deactivation_with_only_global_aliases_is_allowed_when_off():
    global_tag = make_tag(application_id="commerce", slug="global-canonical-2")
    make_alias(tag=global_tag, application_id="commerce", slug="global-alias")
    assert deactivate_tag(global_tag) is True


@override_settings(NAMESPACE_WRITE_ENABLED=True)
def test_global_assignment_helper_rejects_a_namespaced_tag():
    # Compatibility backstop: even with writes enabled, a direct helper call must not
    # insert a global assignment referencing a merchant tag (a cross-namespace ref).
    merchant_tag = make_tag(
        application_id="commerce",
        namespace_type="merchant",
        namespace_id="merchant_a",
        slug="merchant-only",
    )
    with pytest.raises(serializers.ValidationError):
        get_or_create_assignment(
            tenant_id="tenant_a",
            application_id="commerce",
            scope_context=GLOBAL_SCOPE,
            resource_type="product",
            resource_id="p1",
            tag=merchant_tag,
        )
