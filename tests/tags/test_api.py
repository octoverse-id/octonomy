from __future__ import annotations

import pytest

from tests.factories import make_tag

pytestmark = pytest.mark.django_db


def test_create_shared_tag(api_client):
    response = api_client.post(
        "/api/v1/tags",
        {
            "name": "Featured",
            "slug": "featured",
            "type": "label",
            "metadata": {},
        },
        format="json",
    )

    assert response.status_code == 201
    payload = response.json()["data"]
    assert payload["tenant_id"] == "tenant_a"
    assert payload["application_id"] is None
    assert payload["slug"] == "featured"


def test_list_tags_filters_to_current_tenant(api_client, other_tenant_client):
    make_tag(tenant_id="tenant_a", slug="visible")
    make_tag(tenant_id="tenant_b", slug="hidden")

    response = api_client.get("/api/v1/tags")

    assert response.status_code == 200
    slugs = {item["slug"] for item in response.json()["data"]}
    assert slugs == {"visible"}


def test_application_filter_includes_shared_by_default(api_client):
    make_tag(slug="shared")
    make_tag(application_id="commerce", slug="sale")
    make_tag(application_id="cms", slug="editorial")

    response = api_client.get("/api/v1/tags?application_id=commerce")

    assert response.status_code == 200
    slugs = {item["slug"] for item in response.json()["data"]}
    assert slugs == {"shared", "sale"}


def test_delete_tag_deactivates(api_client):
    tag = make_tag(slug="archived")

    response = api_client.delete(f"/api/v1/tags/{tag.id}")
    tag.refresh_from_db()

    assert response.status_code == 204
    assert tag.is_active is False


def test_duplicate_active_slug_returns_conflict(api_client):
    make_tag(slug="featured", type="label")

    response = api_client.post(
        "/api/v1/tags",
        {"name": "Featured Again", "slug": "featured", "type": "label", "metadata": {}},
        format="json",
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"


def test_missing_tenant_uses_error_envelope(client, service_token):
    response = client.get("/api/v1/tags", HTTP_AUTHORIZATION=f"Bearer {service_token}")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"


def test_patch_can_move_tag_between_applications(api_client):
    # The application filter on detail lookups is bound to the caller's authorized
    # applications, not the request body, so a tenant-wide grant can still fetch a
    # tag it is moving to another application (the body names the destination).
    tag = make_tag(application_id="commerce", slug="movable")

    response = api_client.patch(
        f"/api/v1/tags/{tag.id}",
        {"application_id": "cms"},
        format="json",
    )

    assert response.status_code == 200
    assert response.json()["data"]["application_id"] == "cms"
