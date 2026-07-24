from __future__ import annotations

import pytest

from tests.factories import make_tag, make_vocabulary

pytestmark = pytest.mark.django_db


def test_create_and_list_vocabulary(api_client):
    response = api_client.post(
        "/api/v1/vocabularies",
        {
            "application_id": "commerce",
            "name": "Product Labels",
            "slug": "product-labels",
            "description": "Labels for catalog products",
            "metadata": {"owner": "catalog"},
        },
        format="json",
    )

    assert response.status_code == 201
    payload = response.json()["data"]
    assert payload["tenant_id"] == "tenant_a"
    assert payload["application_id"] == "commerce"
    assert payload["slug"] == "product-labels"

    list_response = api_client.get("/api/v1/vocabularies?application_id=commerce")
    assert list_response.status_code == 200
    assert [item["slug"] for item in list_response.json()["data"]] == ["product-labels"]


def test_vocabulary_list_includes_shared_by_default(api_client):
    make_vocabulary(slug="shared-labels")
    make_vocabulary(application_id="commerce", slug="commerce-labels")
    make_vocabulary(application_id="cms", slug="cms-labels")

    response = api_client.get("/api/v1/vocabularies?application_id=commerce")

    assert response.status_code == 200
    assert {item["slug"] for item in response.json()["data"]} == {
        "shared-labels",
        "commerce-labels",
    }


def test_duplicate_active_vocabulary_slug_returns_conflict(api_client):
    make_vocabulary(application_id="commerce", slug="product-labels")

    response = api_client.post(
        "/api/v1/vocabularies",
        {
            "application_id": "commerce",
            "name": "Product Labels Again",
            "slug": "product-labels",
            "metadata": {},
        },
        format="json",
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"


def test_update_vocabulary_and_duplicate_slug_conflict(api_client):
    vocabulary = make_vocabulary(application_id="commerce", slug="product-labels")
    make_vocabulary(application_id="commerce", slug="sale-labels")

    update_response = api_client.patch(
        f"/api/v1/vocabularies/{vocabulary.id}",
        {"name": "Catalog Labels"},
        format="json",
    )
    duplicate_response = api_client.patch(
        f"/api/v1/vocabularies/{vocabulary.id}",
        {"slug": "sale-labels"},
        format="json",
    )

    assert update_response.status_code == 200
    assert update_response.json()["data"]["name"] == "Catalog Labels"
    assert duplicate_response.status_code == 409


def test_cannot_change_vocabulary_application_scope(api_client):
    # Scope is immutable (NS-1): a vocabulary cannot move applications, whether or
    # not tags reference it.
    vocabulary = make_vocabulary(slug="labels")
    make_tag(slug="featured", vocabulary=vocabulary)
    original_application_id = vocabulary.application_id

    response = api_client.patch(
        f"/api/v1/vocabularies/{vocabulary.id}",
        {"application_id": "commerce"},
        format="json",
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "scope_immutable"
    assert "application_id" in response.json()["error"]["details"]
    vocabulary.refresh_from_db()
    assert vocabulary.application_id == original_application_id


def test_delete_vocabulary_deactivates_without_deactivating_tags(api_client):
    vocabulary = make_vocabulary(slug="labels")
    tag = make_tag(slug="featured", vocabulary=vocabulary)

    response = api_client.delete(f"/api/v1/vocabularies/{vocabulary.id}")
    vocabulary.refresh_from_db()
    tag.refresh_from_db()

    assert response.status_code == 204
    assert vocabulary.is_active is False
    assert tag.is_active is True


def test_tag_can_be_patched_after_current_vocabulary_is_inactive(api_client):
    vocabulary = make_vocabulary(slug="labels")
    tag = make_tag(slug="featured", vocabulary=vocabulary)
    vocabulary.is_active = False
    vocabulary.save(update_fields=["is_active", "updated_at"])

    response = api_client.patch(
        f"/api/v1/tags/{tag.id}",
        {"name": "Featured Content"},
        format="json",
    )

    assert response.status_code == 200
    assert response.json()["data"]["name"] == "Featured Content"
    assert response.json()["data"]["vocabulary_id"] == str(vocabulary.id)


def test_create_tag_in_vocabulary_and_filter_tags(api_client):
    vocabulary = make_vocabulary(application_id="commerce", slug="product-labels")

    create_response = api_client.post(
        "/api/v1/tags",
        {
            "application_id": "commerce",
            "name": "Sale",
            "slug": "sale",
            "type": "label",
            "vocabulary_id": str(vocabulary.id),
            "metadata": {},
        },
        format="json",
    )
    list_response = api_client.get(f"/api/v1/tags?vocabulary_id={vocabulary.id}")

    assert create_response.status_code == 201
    assert create_response.json()["data"]["vocabulary_id"] == str(vocabulary.id)
    assert [item["slug"] for item in list_response.json()["data"]] == ["sale"]


def test_patch_tag_can_change_vocabulary(api_client):
    first = make_vocabulary(application_id="commerce", slug="product-labels")
    second = make_vocabulary(application_id="commerce", slug="merchandising-labels")
    tag = make_tag(application_id="commerce", slug="sale", vocabulary=first)

    response = api_client.patch(
        f"/api/v1/tags/{tag.id}",
        {"vocabulary_id": str(second.id)},
        format="json",
    )

    assert response.status_code == 200
    assert response.json()["data"]["vocabulary_id"] == str(second.id)


def test_shared_tag_cannot_use_app_specific_vocabulary(api_client):
    vocabulary = make_vocabulary(application_id="commerce", slug="product-labels")

    response = api_client.post(
        "/api/v1/tags",
        {
            "name": "Featured",
            "slug": "featured",
            "type": "label",
            "vocabulary_id": str(vocabulary.id),
            "metadata": {},
        },
        format="json",
    )

    assert response.status_code == 400
    assert response.json()["error"]["details"]["vocabulary_id"] == [
        "Shared tags can only use shared vocabularies."
    ]


def test_app_tag_cannot_use_other_app_vocabulary(api_client):
    vocabulary = make_vocabulary(application_id="cms", slug="cms-labels")

    response = api_client.post(
        "/api/v1/tags",
        {
            "application_id": "commerce",
            "name": "Sale",
            "slug": "sale",
            "type": "label",
            "vocabulary_id": str(vocabulary.id),
            "metadata": {},
        },
        format="json",
    )

    assert response.status_code == 400
    assert response.json()["error"]["details"]["vocabulary_id"] == [
        "Vocabulary application is incompatible."
    ]


def test_inactive_vocabulary_cannot_be_used_for_new_tag(api_client):
    vocabulary = make_vocabulary(slug="labels", is_active=False)

    response = api_client.post(
        "/api/v1/tags",
        {
            "name": "Featured",
            "slug": "featured",
            "type": "label",
            "vocabulary_id": str(vocabulary.id),
            "metadata": {},
        },
        format="json",
    )

    assert response.status_code == 400
    assert response.json()["error"]["details"]["vocabulary_id"] == [
        "Inactive vocabularies cannot be assigned to tags."
    ]


def test_vocabulary_detail_is_tenant_scoped(api_client):
    vocabulary = make_vocabulary(tenant_id="tenant_b", slug="hidden")

    response = api_client.get(f"/api/v1/vocabularies/{vocabulary.id}")

    assert response.status_code == 404
