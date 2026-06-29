from __future__ import annotations

import pytest
from rest_framework import serializers

from octonomy.assignments.models import TagAssignment
from octonomy.assignments.serializers import BulkAssignSerializer
from octonomy.assignments.services import assign_tag
from octonomy.core.auth import GLOBAL_SCOPE, ScopeContext
from octonomy.core.errors import DomainError
from octonomy.tags.alias_selectors import active_aliases_for_resolution_bulk
from octonomy.tags.alias_services import create_tag_alias, resolve_tag_reference
from octonomy.tags.selectors import filter_tags, tags_for_tenant
from octonomy.tags.services import create_tag
from tests.factories import make_alias, make_tag, make_vocabulary

pytestmark = pytest.mark.django_db

MERCHANT_A = ScopeContext("merchant", "merchant_a")
MERCHANT_B = ScopeContext("merchant", "merchant_b")


def test_parent_and_vocabulary_namespace_compatibility_matrix():
    global_parent = make_tag(application_id="commerce", slug="global-parent")
    merchant_parent = make_tag(
        application_id="commerce",
        namespace_type="merchant",
        namespace_id="merchant_a",
        slug="merchant-parent",
    )
    other_merchant_parent = make_tag(
        application_id="commerce",
        namespace_type="merchant",
        namespace_id="merchant_b",
        slug="other-merchant-parent",
    )
    global_vocabulary = make_vocabulary(application_id="commerce", slug="global-vocab")
    merchant_vocabulary = make_vocabulary(
        application_id="commerce",
        namespace_type="merchant",
        namespace_id="merchant_a",
        slug="merchant-vocab",
    )
    other_merchant_vocabulary = make_vocabulary(
        application_id="commerce",
        namespace_type="merchant",
        namespace_id="merchant_b",
        slug="other-merchant-vocab",
    )

    global_child = create_tag(
        "tenant_a",
        {
            "application_id": "commerce",
            "name": "Global Child",
            "slug": "global-child",
            "type": "label",
            "parent": global_parent,
            "vocabulary": global_vocabulary,
            "metadata": {},
            "is_active": True,
        },
    )
    merchant_child_from_global = create_tag(
        "tenant_a",
        {
            "application_id": "commerce",
            "namespace_type": "merchant",
            "namespace_id": "merchant_a",
            "name": "Merchant Child From Global",
            "slug": "merchant-child-global",
            "type": "label",
            "parent": global_parent,
            "vocabulary": global_vocabulary,
            "metadata": {},
            "is_active": True,
        },
    )
    merchant_child_from_same_namespace = create_tag(
        "tenant_a",
        {
            "application_id": "commerce",
            "namespace_type": "merchant",
            "namespace_id": "merchant_a",
            "name": "Merchant Child From Same Namespace",
            "slug": "merchant-child-same",
            "type": "label",
            "parent": merchant_parent,
            "vocabulary": merchant_vocabulary,
            "metadata": {},
            "is_active": True,
        },
    )

    assert global_child.parent_id == global_parent.id
    assert merchant_child_from_global.vocabulary_id == global_vocabulary.id
    assert merchant_child_from_same_namespace.parent_id == merchant_parent.id

    with pytest.raises(DomainError):
        create_tag(
            "tenant_a",
            {
                "application_id": "commerce",
                "name": "Invalid Global Parent",
                "slug": "invalid-global-parent",
                "type": "label",
                "parent": merchant_parent,
                "metadata": {},
                "is_active": True,
            },
        )
    with pytest.raises(DomainError):
        create_tag(
            "tenant_a",
            {
                "application_id": "commerce",
                "name": "Invalid Global Vocabulary",
                "slug": "invalid-global-vocab",
                "type": "label",
                "vocabulary": merchant_vocabulary,
                "metadata": {},
                "is_active": True,
            },
        )
    with pytest.raises(DomainError):
        create_tag(
            "tenant_a",
            {
                "application_id": "commerce",
                "namespace_type": "merchant",
                "namespace_id": "merchant_a",
                "name": "Invalid Merchant Parent",
                "slug": "invalid-merchant-parent",
                "type": "label",
                "parent": other_merchant_parent,
                "metadata": {},
                "is_active": True,
            },
        )
    with pytest.raises(DomainError):
        create_tag(
            "tenant_a",
            {
                "application_id": "commerce",
                "namespace_type": "merchant",
                "namespace_id": "merchant_a",
                "name": "Invalid Merchant Vocabulary",
                "slug": "invalid-merchant-vocab",
                "type": "label",
                "vocabulary": other_merchant_vocabulary,
                "metadata": {},
                "is_active": True,
            },
        )


def test_alias_namespace_compatibility_matrix():
    global_tag = make_tag(application_id="commerce", slug="global-target")
    merchant_tag = make_tag(
        application_id="commerce",
        namespace_type="merchant",
        namespace_id="merchant_a",
        slug="merchant-target",
    )
    other_merchant_tag = make_tag(
        application_id="commerce",
        namespace_type="merchant",
        namespace_id="merchant_b",
        slug="other-merchant-target",
    )

    global_alias = create_tag_alias(
        "tenant_a",
        {
            "application_id": "commerce",
            "tag": global_tag,
            "name": "Global Deal",
            "slug": "global-deal",
            "metadata": {},
            "is_active": True,
        },
    )
    merchant_alias_to_global = create_tag_alias(
        "tenant_a",
        {
            "application_id": "commerce",
            "namespace_type": "merchant",
            "namespace_id": "merchant_a",
            "tag": global_tag,
            "name": "Merchant Global Deal",
            "slug": "merchant-global-deal",
            "metadata": {},
            "is_active": True,
        },
    )
    merchant_alias_to_same_namespace = create_tag_alias(
        "tenant_a",
        {
            "application_id": "commerce",
            "namespace_type": "merchant",
            "namespace_id": "merchant_a",
            "tag": merchant_tag,
            "name": "Merchant Deal",
            "slug": "merchant-deal",
            "metadata": {},
            "is_active": True,
        },
    )

    assert global_alias.tag_id == global_tag.id
    assert merchant_alias_to_global.tag_id == global_tag.id
    assert merchant_alias_to_same_namespace.tag_id == merchant_tag.id

    with pytest.raises(DomainError):
        create_tag_alias(
            "tenant_a",
            {
                "application_id": "commerce",
                "tag": merchant_tag,
                "name": "Invalid Global Alias",
                "slug": "invalid-global-alias",
                "metadata": {},
                "is_active": True,
            },
        )
    with pytest.raises(DomainError):
        create_tag_alias(
            "tenant_a",
            {
                "application_id": "commerce",
                "namespace_type": "merchant",
                "namespace_id": "merchant_a",
                "tag": other_merchant_tag,
                "name": "Invalid Merchant Alias",
                "slug": "invalid-merchant-alias",
                "metadata": {},
                "is_active": True,
            },
        )


def test_assignment_namespace_compatibility_matrix():
    global_tag = make_tag(application_id="commerce", slug="global-assignable")
    merchant_tag = make_tag(
        application_id="commerce",
        namespace_type="merchant",
        namespace_id="merchant_a",
        slug="merchant-assignable",
    )

    global_assignment = assign_tag(
        "tenant_a",
        "commerce",
        global_tag,
        "product",
        "prod_global",
        scope_context=GLOBAL_SCOPE,
    )
    merchant_global_assignment = assign_tag(
        "tenant_a",
        "commerce",
        global_tag,
        "product",
        "prod_merchant_global",
        scope_context=MERCHANT_A,
    )
    merchant_assignment = assign_tag(
        "tenant_a",
        "commerce",
        merchant_tag,
        "product",
        "prod_merchant",
        scope_context=MERCHANT_A,
    )

    assert global_assignment.assignment.namespace_type is None
    assert merchant_global_assignment.assignment.namespace_id == "merchant_a"
    assert merchant_assignment.assignment.tag_id == merchant_tag.id

    with pytest.raises(serializers.ValidationError):
        assign_tag(
            "tenant_a",
            "commerce",
            merchant_tag,
            "product",
            "prod_global_invalid",
            scope_context=GLOBAL_SCOPE,
        )
    with pytest.raises(serializers.ValidationError):
        assign_tag(
            "tenant_a",
            "commerce",
            merchant_tag,
            "product",
            "prod_merchant_invalid",
            scope_context=MERCHANT_B,
        )


def test_alias_resolution_prefers_merchant_alias_and_scope_qualifier_pins_resolution():
    global_target = make_tag(application_id="commerce", slug="global-target")
    merchant_target = make_tag(
        application_id="commerce",
        namespace_type="merchant",
        namespace_id="merchant_a",
        slug="merchant-target",
    )
    make_alias(tag=global_target, application_id="commerce", slug="deal")
    make_alias(
        tag=merchant_target,
        application_id="commerce",
        namespace_type="merchant",
        namespace_id="merchant_a",
        slug="deal",
    )

    default = resolve_tag_reference(
        "tenant_a",
        "deal",
        "commerce",
        scope_context=MERCHANT_A,
    )
    global_pinned = resolve_tag_reference(
        "tenant_a",
        "deal",
        "commerce",
        scope_context=MERCHANT_A,
        scope_qualifier="global",
    )
    merchant_pinned = resolve_tag_reference(
        "tenant_a",
        "deal",
        "commerce",
        scope_context=MERCHANT_A,
        scope_qualifier="merchant",
    )

    assert default["tag"].id == merchant_target.id
    assert global_pinned["tag"].id == global_target.id
    assert merchant_pinned["tag"].id == merchant_target.id


def test_no_application_canonical_resolution_prefers_merchant_namespace_tag():
    make_tag(slug="scoped-canonical")
    merchant_tag = make_tag(
        application_id="commerce",
        namespace_type="merchant",
        namespace_id="merchant_a",
        slug="scoped-canonical",
    )

    result = resolve_tag_reference(
        "tenant_a",
        "scoped-canonical",
        None,
        scope_context=MERCHANT_A,
    )

    assert result["matched_type"] == "tag"
    assert result["tag"].id == merchant_tag.id


def test_no_application_alias_resolution_prefers_merchant_namespace_alias():
    global_target = make_tag(slug="global-no-app-target")
    merchant_target = make_tag(
        application_id="commerce",
        namespace_type="merchant",
        namespace_id="merchant_a",
        slug="merchant-no-app-target",
    )
    make_alias(tag=global_target, slug="no-app-deal")
    make_alias(
        tag=merchant_target,
        application_id="commerce",
        namespace_type="merchant",
        namespace_id="merchant_a",
        slug="no-app-deal",
    )

    result = resolve_tag_reference(
        "tenant_a",
        "no-app-deal",
        None,
        scope_context=MERCHANT_A,
    )

    assert result["matched_type"] == "alias"
    assert result["tag"].id == merchant_target.id


def test_bulk_alias_resolution_uses_deterministic_namespace_precedence():
    global_target = make_tag(application_id="commerce", slug="global-bulk-target")
    merchant_target = make_tag(
        application_id="commerce",
        namespace_type="merchant",
        namespace_id="merchant_a",
        slug="merchant-bulk-target",
    )
    make_alias(tag=global_target, application_id="commerce", slug="bulk-deal")
    make_alias(
        tag=merchant_target,
        application_id="commerce",
        namespace_type="merchant",
        namespace_id="merchant_a",
        slug="bulk-deal",
    )

    serializer = BulkAssignSerializer(
        data={
            "application_id": "commerce",
            "resource_type": "product",
            "resource_id": "prod_123",
            "alias_slugs": ["bulk-deal"],
        },
        context={"tenant_id": "tenant_a", "scope_context": MERCHANT_A},
    )

    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["tag_ids"] == [merchant_target.id]


def test_no_application_bulk_alias_resolution_uses_namespace_precedence():
    global_target = make_tag(slug="global-bulk-no-app-target")
    merchant_target = make_tag(
        application_id="commerce",
        namespace_type="merchant",
        namespace_id="merchant_a",
        slug="merchant-bulk-no-app-target",
    )
    make_alias(tag=global_target, slug="bulk-no-app-deal")
    make_alias(
        tag=merchant_target,
        application_id="commerce",
        namespace_type="merchant",
        namespace_id="merchant_a",
        slug="bulk-no-app-deal",
    )

    aliases = list(
        active_aliases_for_resolution_bulk(
            "tenant_a",
            ["bulk-no-app-deal"],
            None,
            MERCHANT_A,
        )
    )

    assert aliases[0].tag_id == merchant_target.id


def test_usage_count_matrix_for_legacy_global_and_visible_scoped_views():
    tag = make_tag(application_id="commerce", slug="counted")
    for scope_context, resource_id in [
        (GLOBAL_SCOPE, "prod_global"),
        (MERCHANT_A, "prod_merchant_a"),
        (MERCHANT_B, "prod_merchant_b"),
    ]:
        assign_tag(
            "tenant_a",
            "commerce",
            tag,
            "product",
            resource_id,
            scope_context=scope_context,
        )

    legacy_global = tags_for_tenant("tenant_a", GLOBAL_SCOPE, usage_count_mode="legacy").get(
        id=tag.id
    )
    visible_global = tags_for_tenant("tenant_a", GLOBAL_SCOPE, usage_count_mode="visible").get(
        id=tag.id
    )
    visible_merchant = tags_for_tenant("tenant_a", MERCHANT_A, usage_count_mode="visible").get(
        id=tag.id
    )

    assert legacy_global.usage_count == 3
    assert visible_global.usage_count == 1
    assert visible_merchant.usage_count == 2


def test_tree_traversal_filters_children_to_visible_namespace():
    global_parent = make_tag(application_id="commerce", slug="parent")
    global_child = make_tag(application_id="commerce", slug="global-child", parent=global_parent)
    merchant_a_child = make_tag(
        application_id="commerce",
        namespace_type="merchant",
        namespace_id="merchant_a",
        slug="merchant-a-child",
        parent=global_parent,
    )
    merchant_b_child = make_tag(
        application_id="commerce",
        namespace_type="merchant",
        namespace_id="merchant_b",
        slug="merchant-b-child",
        parent=global_parent,
    )

    queryset = filter_tags(
        tags_for_tenant("tenant_a", MERCHANT_A),
        {"application_id": "commerce", "parent_id": str(global_parent.id)},
    )

    assert set(queryset.values_list("id", flat=True)) == {
        global_child.id,
        merchant_a_child.id,
    }
    assert merchant_b_child.id not in set(queryset.values_list("id", flat=True))


def test_namespaced_assignment_retry_requeries_same_scope(monkeypatch):
    tag = make_tag(application_id="commerce", slug="retry-tag")
    merchant_a_assignment = TagAssignment.objects.create(
        tenant_id="tenant_a",
        application_id="commerce",
        namespace_type="merchant",
        namespace_id="merchant_a",
        tag=tag,
        resource_type="product",
        resource_id="prod_123",
    )
    TagAssignment.objects.create(
        tenant_id="tenant_a",
        application_id="commerce",
        namespace_type="merchant",
        namespace_id="merchant_b",
        tag=tag,
        resource_type="product",
        resource_id="prod_123",
    )

    def raise_integrity_error(*args, **kwargs):
        from django.db import IntegrityError

        raise IntegrityError("simulated race")

    monkeypatch.setattr(TagAssignment.objects, "get_or_create", raise_integrity_error)

    result = assign_tag(
        "tenant_a",
        "commerce",
        tag,
        "product",
        "prod_123",
        scope_context=MERCHANT_A,
    )

    assert result.created is False
    assert result.assignment.id == merchant_a_assignment.id
