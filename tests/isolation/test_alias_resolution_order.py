"""Duplicate-slug-across-scopes resolution order (issue #44, eng review).

When the same slug exists in both a merchant namespace and the global scope and a
caller can see both (a wildcard grant opting into global), resolution must be
deterministic: the merchant's own row wins. The global row is created *first* in
every scenario, so a win by the merchant row proves the resolution-priority
ordering rather than an incidental insertion/pk order.

Covers single resolution (``/tag-resolution``) and the bulk resolution path
(``bulk-assign`` with ``alias_slugs``), which use different selectors
(``active_aliases_for_resolution`` vs ``active_aliases_for_resolution_bulk``).
"""

from __future__ import annotations

import pytest
from django.test import override_settings
from rest_framework.test import APIClient

from octonomy.assignments.models import TagAssignment
from tests.factories import make_alias, make_tag
from tests.isolation.registry import APP

pytestmark = pytest.mark.django_db

NS_A = {"namespace_type": "merchant", "namespace_id": "merchant_a"}


def _wildcard_a_client(wildcard_token) -> APIClient:
    client = APIClient()
    client.credentials(
        HTTP_AUTHORIZATION=f"Bearer {wildcard_token}",
        HTTP_X_TENANT_ID="tenant_a",
        HTTP_X_NAMESPACE_TYPE="merchant",
        HTTP_X_NAMESPACE_ID="merchant_a",
    )
    return client


def test_tag_slug_resolves_to_merchant_before_global(wildcard_token):
    # Global row first: if resolution keyed on insertion order it would win.
    global_tag = make_tag(application_id=APP, slug="dupcanon", name="Global Canon")
    merchant_tag = make_tag(application_id=APP, slug="dupcanon", name="Merchant Canon", **NS_A)

    client = _wildcard_a_client(wildcard_token)
    response = client.get(
        f"/api/v2/tag-resolution?slug=dupcanon&application_id={APP}&include_global=true"
    )

    assert response.status_code == 200, response.data
    resolved = response.json()["data"]["tag"]["id"]
    assert resolved == str(merchant_tag.id)
    assert resolved != str(global_tag.id)


def test_alias_slug_resolves_to_merchant_before_global(wildcard_token):
    # A slug that exists only as aliases (no canonical tag), in both scopes, so
    # the alias resolution path is what decides.
    global_target = make_tag(application_id=APP, slug="dupalias-global-target")
    make_alias(tag=global_target, application_id=APP, slug="dupaliasonly", name="Global Alias")
    merchant_target = make_tag(application_id=APP, slug="dupalias-merchant-target", **NS_A)
    make_alias(
        tag=merchant_target, application_id=APP, slug="dupaliasonly", name="Merchant Alias", **NS_A
    )

    client = _wildcard_a_client(wildcard_token)
    response = client.get(
        f"/api/v2/tag-resolution?slug=dupaliasonly&application_id={APP}&include_global=true"
    )

    assert response.status_code == 200, response.data
    assert response.json()["data"]["tag"]["id"] == str(merchant_target.id)


@override_settings(NAMESPACE_WRITE_ENABLED=True)
def test_bulk_alias_slug_resolves_to_merchant_before_global(wildcard_token):
    global_target = make_tag(application_id=APP, slug="bulk-global-target")
    make_alias(tag=global_target, application_id=APP, slug="bulkdupalias", name="Global Bulk Alias")
    merchant_target = make_tag(application_id=APP, slug="bulk-merchant-target", **NS_A)
    make_alias(
        tag=merchant_target,
        application_id=APP,
        slug="bulkdupalias",
        name="Merchant Bulk Alias",
        **NS_A,
    )

    client = _wildcard_a_client(wildcard_token)
    response = client.post(
        "/api/v2/tag-assignments/bulk-assign",
        {
            "application_id": APP,
            "alias_slugs": ["bulkdupalias"],
            "resource_type": "product",
            "resource_id": "bulk-resolve-order",
            "include_global": True,
        },
        format="json",
    )

    assert response.status_code in (200, 201), response.data
    assignments = TagAssignment.objects.filter(resource_id="bulk-resolve-order")
    assert assignments.count() == 1
    # The bulk path resolved the shared slug to the merchant's alias target, not
    # the global one, and landed the assignment in the merchant namespace.
    assignment = assignments.get()
    assert assignment.tag_id == merchant_target.id
    assert (assignment.namespace_type, assignment.namespace_id) == ("merchant", "merchant_a")
