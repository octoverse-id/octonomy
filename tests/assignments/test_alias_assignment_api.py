from __future__ import annotations

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from octonomy.assignments.models import TagAssignment
from octonomy.assignments.serializers import BulkAssignSerializer
from tests.factories import make_alias, make_tag

pytestmark = pytest.mark.django_db


def test_assign_tag_by_alias_id(api_client):
    tag = make_tag(application_id="commerce", slug="sale")
    alias = make_alias(tag=tag, application_id="commerce", slug="promo")

    response = api_client.post(
        "/api/v1/tag-assignments",
        {
            "application_id": "commerce",
            "alias_id": str(alias.id),
            "resource_type": "product",
            "resource_id": "prod_123",
        },
        format="json",
    )

    assert response.status_code == 201
    assert TagAssignment.objects.get().tag_id == tag.id


def test_assign_tag_by_alias_slug_is_idempotent(api_client):
    tag = make_tag(slug="featured")
    make_alias(tag=tag, slug="hero")
    payload = {
        "application_id": "commerce",
        "alias_slug": "hero",
        "resource_type": "product",
        "resource_id": "prod_123",
    }

    first = api_client.post("/api/v1/tag-assignments", payload, format="json")
    second = api_client.post("/api/v1/tag-assignments", payload, format="json")

    assert first.status_code == 201
    assert second.status_code == 200
    assert TagAssignment.objects.count() == 1


def test_bulk_assign_and_replace_resource_tags_by_alias_slug(api_client):
    featured = make_tag(slug="featured")
    sale = make_tag(application_id="commerce", slug="sale")
    make_alias(tag=featured, slug="hero")
    make_alias(tag=sale, application_id="commerce", slug="promo")

    bulk = api_client.post(
        "/api/v1/tag-assignments/bulk-assign",
        {
            "application_id": "commerce",
            "resource_type": "product",
            "resource_id": "prod_123",
            "alias_slugs": ["hero", "promo"],
        },
        format="json",
    )
    replace = api_client.post(
        "/api/v1/resources/product/prod_123/tags",
        {
            "application_id": "commerce",
            "tag_ids": [str(featured.id)],
            "alias_slugs": ["promo"],
        },
        format="json",
    )

    assert bulk.status_code == 200
    assert bulk.json()["data"]["created"] == 2
    assert replace.status_code == 200
    assert {item["slug"] for item in replace.json()["data"]["tags"]} == {"featured", "sale"}


def test_bulk_alias_slug_resolution_uses_one_query():
    featured = make_tag(slug="featured")
    sale = make_tag(application_id="commerce", slug="sale")
    make_alias(tag=featured, slug="hero")
    make_alias(tag=sale, application_id="commerce", slug="promo")
    serializer = BulkAssignSerializer(
        data={
            "application_id": "commerce",
            "resource_type": "product",
            "resource_id": "prod_123",
            "alias_slugs": ["hero", "promo"],
        },
        context={"tenant_id": "tenant_a"},
    )

    with CaptureQueriesContext(connection) as queries:
        assert serializer.is_valid(), serializer.errors

    assert len(queries) == 1
    assert set(serializer.validated_data["tag_ids"]) == {featured.id, sale.id}


def test_bulk_alias_slug_resolution_checks_size_before_query(settings):
    settings.MAX_BULK_TAGS = 1
    serializer = BulkAssignSerializer(
        data={
            "application_id": "commerce",
            "resource_type": "product",
            "resource_id": "prod_123",
            "alias_slugs": ["hero", "promo"],
        },
        context={"tenant_id": "tenant_a"},
    )

    with CaptureQueriesContext(connection) as queries:
        assert not serializer.is_valid()

    assert len(queries) == 0
    assert serializer.errors["alias_slugs"] == ["Maximum bulk size is 1."]


def test_alias_assignment_rejects_other_application_and_inactive_alias(api_client):
    sale = make_tag(application_id="commerce", slug="sale")
    make_alias(tag=sale, application_id="commerce", slug="promo")
    inactive_alias = make_alias(tag=make_tag(slug="featured"), slug="inactive", is_active=False)
    inactive_tag_alias = make_alias(tag=make_tag(slug="archived", is_active=False), slug="legacy")
    other_tenant_alias = make_alias(
        tag=make_tag(tenant_id="tenant_b", slug="external"),
        tenant_id="tenant_b",
        slug="external",
    )

    wrong_app = api_client.post(
        "/api/v1/tag-assignments",
        {
            "application_id": "cms",
            "alias_slug": "promo",
            "resource_type": "article",
            "resource_id": "article_123",
        },
        format="json",
    )
    inactive = api_client.post(
        "/api/v1/tag-assignments",
        {
            "application_id": "commerce",
            "alias_slug": "inactive",
            "resource_type": "product",
            "resource_id": "prod_123",
        },
        format="json",
    )
    inactive_by_id = api_client.post(
        "/api/v1/tag-assignments",
        {
            "application_id": "commerce",
            "alias_id": str(inactive_alias.id),
            "resource_type": "product",
            "resource_id": "prod_456",
        },
        format="json",
    )
    inactive_tag = api_client.post(
        "/api/v1/tag-assignments",
        {
            "application_id": "commerce",
            "alias_id": str(inactive_tag_alias.id),
            "resource_type": "product",
            "resource_id": "prod_654",
        },
        format="json",
    )
    other_tenant = api_client.post(
        "/api/v1/tag-assignments",
        {
            "application_id": "commerce",
            "alias_id": str(other_tenant_alias.id),
            "resource_type": "product",
            "resource_id": "prod_789",
        },
        format="json",
    )

    assert wrong_app.status_code == 400
    assert inactive.status_code == 400
    assert inactive.json()["error"]["details"]["alias_slug"] == ["Alias was not found."]
    assert inactive_by_id.status_code == 400
    assert inactive_by_id.json()["error"]["details"]["alias_id"] == [
        "Inactive aliases cannot be assigned."
    ]
    assert inactive_tag.status_code == 400
    assert inactive_tag.json()["error"]["details"]["alias_id"] == ["Alias tag is inactive."]
    assert other_tenant.status_code == 400
    assert other_tenant.json()["error"]["details"]["alias_id"] == ["Alias was not found."]


def test_tag_id_and_alias_fields_are_mutually_exclusive(api_client):
    tag = make_tag(slug="featured")
    make_alias(tag=tag, slug="hero")

    response = api_client.post(
        "/api/v1/tag-assignments",
        {
            "application_id": "commerce",
            "tag_id": str(tag.id),
            "alias_slug": "hero",
            "resource_type": "product",
            "resource_id": "prod_123",
        },
        format="json",
    )

    assert response.status_code == 400
    assert response.json()["error"]["details"]["non_field_errors"] == [
        "Provide exactly one of tag_id, alias_id, or alias_slug."
    ]


def test_bulk_assign_requires_tag_or_alias_identifiers(api_client):
    response = api_client.post(
        "/api/v1/tag-assignments/bulk-assign",
        {
            "application_id": "commerce",
            "resource_type": "product",
            "resource_id": "prod_123",
        },
        format="json",
    )

    assert response.status_code == 400
    assert response.json()["error"]["details"]["non_field_errors"] == [
        "Provide tag_ids, alias_slugs, or both."
    ]
