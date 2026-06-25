from __future__ import annotations

import pytest

from octonomy.assignments.models import TagAssignment
from octonomy.assignments.services import (
    assign_tag,
    bulk_assign_tags,
    remove_tag_assignment,
    replace_resource_tags,
)
from octonomy.core.errors import ApplicationMismatchError, InactiveTagError
from tests.factories import make_tag

pytestmark = pytest.mark.django_db


def test_inactive_tag_cannot_be_assigned():
    tag = make_tag(slug="archived", is_active=False)

    with pytest.raises(InactiveTagError):
        assign_tag("tenant_a", "commerce", tag, "product", "prod_123")


def test_application_mismatch_rejected():
    tag = make_tag(application_id="commerce", slug="sale")

    with pytest.raises(ApplicationMismatchError):
        assign_tag("tenant_a", "cms", tag, "article", "article_123")


def test_legacy_assignment_write_does_not_reuse_namespaced_assignment():
    tag = make_tag(application_id="commerce", slug="sale")
    namespaced = TagAssignment.objects.create(
        tenant_id="tenant_a",
        application_id="commerce",
        namespace_type="merchant",
        namespace_id="merchant_a",
        tag=tag,
        resource_type="product",
        resource_id="prod_123",
    )

    first = assign_tag("tenant_a", "commerce", tag, "product", "prod_123")
    second = assign_tag("tenant_a", "commerce", tag, "product", "prod_123")

    assert first.created is True
    assert first.assignment.namespace_type is None
    assert first.assignment.namespace_id is None
    assert second.created is False
    assert second.assignment == first.assignment
    assert TagAssignment.objects.filter(id=namespaced.id).exists()


def test_legacy_bulk_assignment_does_not_reuse_namespaced_assignment():
    tag = make_tag(application_id="commerce", slug="sale")
    TagAssignment.objects.create(
        tenant_id="tenant_a",
        application_id="commerce",
        namespace_type="merchant",
        namespace_id="merchant_a",
        tag=tag,
        resource_type="product",
        resource_id="prod_123",
    )

    result = bulk_assign_tags(
        "tenant_a",
        "commerce",
        "product",
        "prod_123",
        [tag.id],
    )

    assert result["created"] == 1
    assert result["existing"] == 0
    assert result["assignments"][0].namespace_type is None
    assert TagAssignment.objects.count() == 2


def test_legacy_remove_and_replace_leave_namespaced_assignments_untouched():
    retained_tag = make_tag(application_id="commerce", slug="retained")
    replacement_tag = make_tag(application_id="commerce", slug="replacement")
    namespaced = TagAssignment.objects.create(
        tenant_id="tenant_a",
        application_id="commerce",
        namespace_type="merchant",
        namespace_id="merchant_a",
        tag=retained_tag,
        resource_type="product",
        resource_id="prod_123",
    )
    global_assignment = assign_tag(
        "tenant_a",
        "commerce",
        retained_tag,
        "product",
        "prod_123",
    ).assignment

    removed = remove_tag_assignment(
        "tenant_a",
        "commerce",
        retained_tag.id,
        "product",
        "prod_123",
    )
    result = replace_resource_tags(
        "tenant_a",
        "commerce",
        "product",
        "prod_123",
        [replacement_tag.id],
    )

    assert removed == 1
    assert not TagAssignment.objects.filter(id=global_assignment.id).exists()
    assert TagAssignment.objects.filter(id=namespaced.id).exists()
    assert result["created"] == 1
    assert result["removed"] == 0
    assert [assignment.tag_id for assignment in result["assignments"]] == [replacement_tag.id]
