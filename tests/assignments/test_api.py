from __future__ import annotations

import pytest

from octonomy.assignments.models import TagAssignment
from tests.factories import make_tag

pytestmark = pytest.mark.django_db


def test_assign_tag_is_idempotent(api_client):
    tag = make_tag(application_id="commerce", slug="sale")
    payload = {
        "application_id": "commerce",
        "tag_id": str(tag.id),
        "resource_type": "product",
        "resource_id": "prod_123",
        "assigned_by": "svc-catalog",
    }

    first = api_client.post("/api/v1/tag-assignments", payload, format="json")
    second = api_client.post("/api/v1/tag-assignments", payload, format="json")

    assert first.status_code == 201
    assert second.status_code == 200
    assert TagAssignment.objects.count() == 1


def test_app_specific_tag_cannot_be_assigned_to_other_app(api_client):
    tag = make_tag(application_id="commerce", slug="sale")

    response = api_client.post(
        "/api/v1/tag-assignments",
        {
            "application_id": "cms",
            "tag_id": str(tag.id),
            "resource_type": "article",
            "resource_id": "article_123",
        },
        format="json",
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "application_mismatch"


def test_shared_tag_can_be_assigned_to_multiple_apps(api_client):
    tag = make_tag(slug="featured")

    for application_id, resource_type, resource_id in [
        ("commerce", "product", "prod_123"),
        ("cms", "article", "article_123"),
    ]:
        response = api_client.post(
            "/api/v1/tag-assignments",
            {
                "application_id": application_id,
                "tag_id": str(tag.id),
                "resource_type": resource_type,
                "resource_id": resource_id,
            },
            format="json",
        )
        assert response.status_code == 201

    assert TagAssignment.objects.count() == 2


def test_bulk_assign_reports_created_and_existing(api_client):
    shared = make_tag(slug="featured")
    sale = make_tag(application_id="commerce", slug="sale")

    payload = {
        "application_id": "commerce",
        "resource_type": "product",
        "resource_id": "prod_123",
        "tag_ids": [str(shared.id), str(sale.id)],
    }

    first = api_client.post("/api/v1/tag-assignments/bulk-assign", payload, format="json")
    second = api_client.post("/api/v1/tag-assignments/bulk-assign", payload, format="json")

    assert first.status_code == 200
    assert first.json()["data"]["created"] == 2
    assert second.json()["data"]["existing"] == 2


def test_replace_resource_tags(api_client):
    featured = make_tag(slug="featured")
    archived = make_tag(slug="archived", type="state")

    first = api_client.post(
        "/api/v1/resources/product/prod_123/tags",
        {
            "application_id": "commerce",
            "tag_ids": [str(featured.id), str(archived.id)],
        },
        format="json",
    )
    second = api_client.post(
        "/api/v1/resources/product/prod_123/tags",
        {
            "application_id": "commerce",
            "tag_ids": [str(featured.id)],
        },
        format="json",
    )

    assert first.status_code == 200
    assert first.json()["data"]["created"] == 2
    assert second.json()["data"]["removed"] == 1
    assert list(TagAssignment.objects.values_list("tag_id", flat=True)) == [featured.id]


def test_list_resource_tags(api_client):
    tag = make_tag(slug="featured")
    api_client.post(
        "/api/v1/tag-assignments",
        {
            "application_id": "commerce",
            "tag_id": str(tag.id),
            "resource_type": "product",
            "resource_id": "prod_123",
        },
        format="json",
    )

    response = api_client.get("/api/v1/resources/product/prod_123/tags?application_id=commerce")

    assert response.status_code == 200
    assert response.json()["data"][0]["tag"]["slug"] == "featured"


def test_list_resources_for_tag(api_client):
    tag = make_tag(slug="featured")
    api_client.post(
        "/api/v1/tag-assignments",
        {
            "application_id": "commerce",
            "tag_id": str(tag.id),
            "resource_type": "product",
            "resource_id": "prod_123",
        },
        format="json",
    )

    response = api_client.get(f"/api/v1/tags/{tag.id}/resources?application_id=commerce")

    assert response.status_code == 200
    assert response.json()["data"][0]["resource_id"] == "prod_123"
