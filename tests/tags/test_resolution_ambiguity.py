"""NS-2: the resolution ladder's tie guard.

Through the HTTP surface the ladder is a strict total order (split unique
constraints + the rule that a namespaced request must name an application), so
these ties are not reachable over the API — a namespaced request without an
application 403s before resolution. The guard exists for direct/internal callers
and as defence-in-depth: a same-rung tie must raise a named error, never resolve to
an arbitrary (name, id) row. These tests call the resolver directly to exercise it.
"""

from __future__ import annotations

import pytest
from rest_framework import serializers

from octonomy.core.auth import ScopeContext
from octonomy.core.errors import AmbiguousResolutionError
from octonomy.tags.alias_services import (
    most_specific_matches,
    raise_canonical_ambiguity,
    resolve_tag_reference,
    unique_resolution_match,
)
from tests.factories import make_alias, make_tag

pytestmark = pytest.mark.django_db

MERCHANT_A = ScopeContext(namespace_type="merchant", namespace_id="merchant_a")


def _ns() -> dict:
    return {"namespace_type": "merchant", "namespace_id": "merchant_a"}


class _Row:
    def __init__(self, resolution_priority=None):
        self.resolution_priority = resolution_priority


def test_unique_resolution_match_empty_returns_none():
    assert unique_resolution_match([], {"application_id": ["x"]}) is None


def test_unique_resolution_match_returns_sole_top_rung_row():
    top = _Row(resolution_priority=0)
    lower = _Row(resolution_priority=1)
    assert unique_resolution_match([top, lower], {"application_id": ["x"]}) is top


def test_unique_resolution_match_raises_on_same_rung_tie():
    rows = [_Row(resolution_priority=0), _Row(resolution_priority=0)]
    with pytest.raises(AmbiguousResolutionError) as exc_info:
        unique_resolution_match(rows, {"application_id": ["provide application_id."]})
    assert exc_info.value.code == "ambiguous_resolution"
    assert exc_info.value.status_code == 400
    assert "application_id" in exc_info.value.details


def test_most_specific_matches_without_priority_returns_all():
    # A queryset that carries no resolution_priority annotation (e.g. the global
    # no-application path) must not be narrowed away to nothing.
    rows = [_Row(), _Row()]
    assert most_specific_matches(rows) == rows


def test_raise_canonical_ambiguity_uses_type_axis_when_types_differ():
    label = make_tag(slug="dup", type="label")
    state = make_tag(slug="dup", type="state")
    with pytest.raises(serializers.ValidationError) as exc_info:
        raise_canonical_ambiguity([label, state], None)
    assert "type" in exc_info.value.detail


def test_raise_canonical_ambiguity_uses_application_axis_for_same_type():
    a = make_tag(application_id="commerce", slug="dup", type="label")
    b = make_tag(application_id="orders", slug="dup", type="label")
    with pytest.raises(AmbiguousResolutionError) as exc_info:
        raise_canonical_ambiguity([a, b], "label")
    assert "application_id" in exc_info.value.details


def test_cross_application_alias_tie_raises_named_error():
    # Same alias slug in two applications, both in merchant_a, no application named.
    # (The alias slug differs from its target tag's slug, so the canonical path is
    # empty and resolution reaches the alias branch.)
    commerce_target = make_tag(application_id="commerce", slug="commerce-target", **_ns())
    orders_target = make_tag(application_id="orders", slug="orders-target", **_ns())
    make_alias(tag=commerce_target, application_id="commerce", slug="dup", **_ns())
    make_alias(tag=orders_target, application_id="orders", slug="dup", **_ns())

    with pytest.raises(AmbiguousResolutionError) as exc_info:
        resolve_tag_reference(
            tenant_id="tenant_a",
            slug="dup",
            application_id=None,
            scope_context=MERCHANT_A,
            authorized_global=True,
        )
    assert exc_info.value.code == "ambiguous_resolution"
    assert "application_id" in exc_info.value.details


def test_cross_application_canonical_tie_raises_named_error():
    make_tag(application_id="commerce", slug="dup", type="label", **_ns())
    make_tag(application_id="orders", slug="dup", type="label", **_ns())

    with pytest.raises(AmbiguousResolutionError) as exc_info:
        resolve_tag_reference(
            tenant_id="tenant_a",
            slug="dup",
            application_id=None,
            scope_context=MERCHANT_A,
            authorized_global=True,
        )
    assert exc_info.value.code == "ambiguous_resolution"
    assert "application_id" in exc_info.value.details


def test_resolution_is_deterministic_when_application_named():
    # With an application named the ladder is a strict total order, so the same
    # cross-application rows resolve without a tie — the merchant/commerce row wins.
    commerce_target = make_tag(application_id="commerce", slug="commerce-target", **_ns())
    make_tag(application_id="orders", slug="orders-target", **_ns())
    make_alias(tag=commerce_target, application_id="commerce", slug="dup", **_ns())
    make_alias(
        tag=make_tag(application_id="orders", slug="orders-target-2", **_ns()),
        application_id="orders",
        slug="dup",
        **_ns(),
    )

    result = resolve_tag_reference(
        tenant_id="tenant_a",
        slug="dup",
        application_id="commerce",
        scope_context=MERCHANT_A,
        authorized_global=True,
    )
    assert result["matched_type"] == "alias"
    assert result["tag"].id == commerce_target.id
