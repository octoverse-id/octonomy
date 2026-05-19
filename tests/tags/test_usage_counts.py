from __future__ import annotations

import pytest

from tests.factories import make_tag

pytestmark = pytest.mark.django_db


def assign(api_client, tag, application_id="commerce", resource_id="prod_123"):
    return api_client.post(
        "/api/v1/tag-assignments",
        {
            "application_id": application_id,
            "tag_id": str(tag.id),
            "resource_type": "product" if application_id == "commerce" else "article",
            "resource_id": resource_id,
        },
        format="json",
    )


def test_tag_list_and_detail_include_computed_usage_count(api_client):
    shared = make_tag(slug="featured")
    sale = make_tag(application_id="commerce", slug="sale")
    assign(api_client, shared, "commerce", "prod_123")
    assign(api_client, shared, "cms", "article_123")
    assign(api_client, sale, "commerce", "prod_456")

    list_response = api_client.get("/api/v1/tags?application_id=commerce")
    detail_response = api_client.get(f"/api/v1/tags/{shared.id}")

    counts = {item["slug"]: item["usage_count"] for item in list_response.json()["data"]}
    assert counts["featured"] == 2
    assert counts["sale"] == 1
    assert detail_response.json()["data"]["usage_count"] == 2


def test_usage_count_is_tenant_isolated(api_client, other_tenant_client):
    tag = make_tag(slug="featured")
    other_tag = make_tag(tenant_id="tenant_b", slug="featured")
    assign(api_client, tag, "commerce", "prod_123")
    other_tenant_client.post(
        "/api/v1/tag-assignments",
        {
            "application_id": "commerce",
            "tag_id": str(other_tag.id),
            "resource_type": "product",
            "resource_id": "prod_999",
        },
        format="json",
    )

    response = api_client.get(f"/api/v1/tags/{tag.id}")

    assert response.json()["data"]["usage_count"] == 1


def test_usage_count_updates_after_remove_and_replace(api_client):
    featured = make_tag(slug="featured")
    archived = make_tag(slug="archived", type="state")
    assign(api_client, featured, "commerce", "prod_123")
    assign(api_client, archived, "commerce", "prod_123")

    api_client.delete(
        "/api/v1/tag-assignments",
        {
            "application_id": "commerce",
            "tag_id": str(archived.id),
            "resource_type": "product",
            "resource_id": "prod_123",
        },
        format="json",
    )
    replace_response = api_client.post(
        "/api/v1/resources/product/prod_456/tags",
        {
            "application_id": "commerce",
            "tag_ids": [str(featured.id)],
        },
        format="json",
    )

    featured_detail = api_client.get(f"/api/v1/tags/{featured.id}")
    archived_detail = api_client.get(f"/api/v1/tags/{archived.id}")

    assert replace_response.json()["data"]["tags"][0]["usage_count"] == 2
    assert featured_detail.json()["data"]["usage_count"] == 2
    assert archived_detail.json()["data"]["usage_count"] == 0


def test_inactive_tag_detail_still_shows_current_usage_count(api_client):
    tag = make_tag(slug="featured")
    assign(api_client, tag, "commerce", "prod_123")
    api_client.delete(f"/api/v1/tags/{tag.id}")

    response = api_client.get(f"/api/v1/tags/{tag.id}")

    assert response.json()["data"]["is_active"] is False
    assert response.json()["data"]["usage_count"] == 1
