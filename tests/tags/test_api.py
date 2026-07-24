from __future__ import annotations

import pytest

from tests.factories import make_tag, make_vocabulary

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


def test_patch_cannot_move_tag_between_applications(api_client):
    # The detail lookup is still bound to the caller's authorized applications, so
    # the tag is found (not 404) even though the body names a different application —
    # but scope is immutable (NS-1), so the move is rejected, not applied.
    tag = make_tag(application_id="commerce", slug="movable")

    response = api_client.patch(
        f"/api/v1/tags/{tag.id}",
        {"application_id": "cms"},
        format="json",
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "scope_immutable"
    assert "application_id" in response.json()["error"]["details"]
    tag.refresh_from_db()
    assert tag.application_id == "commerce"


def test_scope_change_returns_immutable_even_with_app_scoped_relations(api_client):
    # The immutability guard runs BEFORE parent/vocabulary compatibility validation:
    # otherwise checking the existing (commerce) vocabulary against the destination
    # (cms) application would 400 first, so the response would depend on attached
    # relations. A scope-changing PATCH must always return 409 scope_immutable.
    vocab = make_vocabulary(application_id="commerce", slug="labels")
    tag = make_tag(application_id="commerce", slug="featured", vocabulary=vocab)

    response = api_client.patch(
        f"/api/v1/tags/{tag.id}",
        {"application_id": "cms"},
        format="json",
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "scope_immutable"
    tag.refresh_from_db()
    assert tag.application_id == "commerce"
