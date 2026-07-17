"""The duplicate-key metric is precise, not conflated (issue #45).

`namespace_conflict` must count only real uniqueness collisions on the namespace-aware
constraints — never business-rule conflicts (which share the `conflict` error code).
"""

from __future__ import annotations

import logging

import pytest
from django.db import IntegrityError
from django.test import override_settings

from octonomy.assignments.services import assign_tag
from octonomy.core.auth import ScopeContext
from octonomy.core.errors import ConflictError
from octonomy.tags import services as tag_services
from octonomy.tags.services import create_tag, update_tag
from tests.factories import make_tag

pytestmark = pytest.mark.django_db

MERCHANT_A = ScopeContext("merchant", "merchant_a")


def _namespace_conflicts(caplog):
    return [r for r in caplog.records if getattr(r, "metric", None) == "namespace_conflict"]


@override_settings(NAMESPACE_WRITE_ENABLED=True)
def test_namespace_conflict_metric_emitted_on_duplicate_namespaced_slug(caplog):
    data = {
        "application_id": "commerce",
        "namespace_type": "merchant",
        "namespace_id": "merchant_a",
        "slug": "dup",
        "type": "label",
        "name": "Dup",
        "metadata": {},
    }
    create_tag("tenant_a", dict(data))

    caplog.set_level(logging.INFO, logger="octonomy.metrics")
    with pytest.raises(ConflictError):
        create_tag("tenant_a", dict(data))

    conflicts = _namespace_conflicts(caplog)
    assert len(conflicts) == 1
    assert conflicts[0].metric_fields["entity"] == "tag"
    assert conflicts[0].metric_fields["namespace_type"] == "merchant"
    assert conflicts[0].metric_fields["namespace_id"] == "merchant_a"


@override_settings(NAMESPACE_WRITE_ENABLED=True)
def test_namespace_conflict_metric_not_emitted_on_scope_move_conflict(caplog):
    # A scope-move-blocked ConflictError shares the `conflict` error code but is NOT a
    # duplicate-key collision, so the dedicated metric must stay silent.
    tag = make_tag(
        application_id="commerce",
        namespace_type="merchant",
        namespace_id="merchant_a",
        slug="scoped",
    )
    assign_tag(
        tenant_id="tenant_a",
        application_id="commerce",
        tag=tag,
        resource_type="product",
        resource_id="p1",
        scope_context=MERCHANT_A,
    )

    caplog.set_level(logging.INFO, logger="octonomy.metrics")
    with pytest.raises(ConflictError):
        update_tag(tag, {"namespace_id": "merchant_b"})

    assert _namespace_conflicts(caplog) == []


@override_settings(NAMESPACE_WRITE_ENABLED=True)
def test_non_uniqueness_integrity_error_is_not_mislabelled(monkeypatch, caplog):
    # An IntegrityError from the audit/outbox writes (not the entity write) must
    # propagate as-is, never be relabelled a duplicate-slug 409 or counted as a
    # namespace_conflict.
    def _boom(*args, **kwargs):
        raise IntegrityError("outbox constraint failure")

    monkeypatch.setattr(tag_services, "create_outbox_event", _boom)

    data = {
        "application_id": "commerce",
        "namespace_type": "merchant",
        "namespace_id": "merchant_a",
        "slug": "fresh",
        "type": "label",
        "name": "Fresh",
        "metadata": {},
    }
    caplog.set_level(logging.INFO, logger="octonomy.metrics")
    with pytest.raises(IntegrityError):
        create_tag("tenant_a", data)

    assert _namespace_conflicts(caplog) == []
