from __future__ import annotations

import pytest
from django.db import OperationalError
from rest_framework.test import APIClient

from octonomy.audit.models import AuditLog
from octonomy.service_auth.services import create_service_client_token
from octonomy.tags.models import TagAlias
from tests.factories import make_alias, make_tag

pytestmark = pytest.mark.django_db


def test_create_list_get_update_and_delete_alias(api_client):
    tag = make_tag(slug="featured")

    create_response = api_client.post(
        "/api/v1/tag-aliases",
        {
            "tag_id": str(tag.id),
            "name": "Promoted",
            "slug": "promoted",
            "metadata": {"source": "cms"},
        },
        format="json",
    )
    alias_id = create_response.json()["data"]["id"]
    list_response = api_client.get("/api/v1/tag-aliases?slug=promoted")
    detail_response = api_client.get(f"/api/v1/tag-aliases/{alias_id}")
    update_response = api_client.patch(
        f"/api/v1/tag-aliases/{alias_id}",
        {"name": "Hero"},
        format="json",
    )
    delete_response = api_client.delete(f"/api/v1/tag-aliases/{alias_id}")
    repeated_delete = api_client.delete(f"/api/v1/tag-aliases/{alias_id}")

    assert create_response.status_code == 201
    assert list_response.status_code == 200
    assert [item["slug"] for item in list_response.json()["data"]] == ["promoted"]
    assert detail_response.json()["data"]["tag_id"] == str(tag.id)
    assert update_response.json()["data"]["name"] == "Hero"
    assert delete_response.status_code == 204
    assert repeated_delete.status_code == 204
    assert TagAlias.objects.get(id=alias_id).is_active is False
    assert list(AuditLog.objects.order_by("created_at").values_list("action", flat=True)) == [
        "tag_alias.created",
        "tag_alias.updated",
        "tag_alias.deactivated",
    ]


def test_cannot_move_alias_between_applications(api_client):
    # Aliases had no scope-move guard before NS-1; scope is now immutable, so a
    # PATCH changing the alias's own application is rejected (re-pointing the tag
    # within scope is still allowed).
    tag = make_tag(application_id="commerce", slug="featured")
    alias = make_alias(tag=tag, application_id="commerce", slug="promoted")

    response = api_client.patch(
        f"/api/v1/tag-aliases/{alias.id}",
        {"application_id": "cms"},
        format="json",
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "scope_immutable"
    assert "application_id" in response.json()["error"]["details"]
    alias.refresh_from_db()
    assert alias.application_id == "commerce"


def test_list_aliases_for_tag(api_client):
    tag = make_tag(slug="featured")
    make_alias(tag=tag, slug="promoted")
    make_alias(slug="hidden")

    response = api_client.get(f"/api/v1/tags/{tag.id}/aliases")

    assert response.status_code == 200
    assert [item["slug"] for item in response.json()["data"]] == ["promoted"]


def test_alias_tenant_and_application_rules(api_client):
    shared = make_tag(slug="featured")
    app_tag = make_tag(application_id="commerce", slug="sale")

    shared_app_alias = api_client.post(
        "/api/v1/tag-aliases",
        {
            "application_id": "commerce",
            "tag_id": str(shared.id),
            "name": "Hero",
            "slug": "hero",
        },
        format="json",
    )
    wrong_app_alias = api_client.post(
        "/api/v1/tag-aliases",
        {
            "application_id": "cms",
            "tag_id": str(app_tag.id),
            "name": "Promo",
            "slug": "promo",
        },
        format="json",
    )
    other_tenant_alias = api_client.post(
        "/api/v1/tag-aliases",
        {
            "tag_id": str(make_tag(tenant_id="tenant_b", slug="external").id),
            "name": "External",
            "slug": "external",
        },
        format="json",
    )

    assert shared_app_alias.status_code == 201
    assert wrong_app_alias.status_code == 400
    assert wrong_app_alias.json()["error"]["code"] == "application_mismatch"
    assert other_tenant_alias.status_code == 400


def test_duplicate_active_alias_slug_returns_conflict(api_client):
    tag = make_tag(slug="featured")
    make_alias(tag=tag, application_id="commerce", slug="hero")

    duplicate = api_client.post(
        "/api/v1/tag-aliases",
        {
            "application_id": "commerce",
            "tag_id": str(tag.id),
            "name": "Hero Again",
            "slug": "hero",
        },
        format="json",
    )

    assert duplicate.status_code == 409


def test_alias_metadata_must_be_object(api_client):
    tag = make_tag(slug="featured")

    response = api_client.post(
        "/api/v1/tag-aliases",
        {"tag_id": str(tag.id), "name": "Promoted", "slug": "promoted", "metadata": []},
        format="json",
    )

    assert response.status_code == 400


def test_alias_endpoints_enforce_tenant_isolation(api_client, other_tenant_client):
    alias = make_alias(slug="promoted")

    list_response = other_tenant_client.get("/api/v1/tag-aliases")
    detail_response = other_tenant_client.get(f"/api/v1/tag-aliases/{alias.id}")
    update_response = other_tenant_client.patch(
        f"/api/v1/tag-aliases/{alias.id}",
        {"name": "Hidden"},
        format="json",
    )
    delete_response = other_tenant_client.delete(f"/api/v1/tag-aliases/{alias.id}")
    cross_tenant_create = other_tenant_client.post(
        "/api/v1/tag-aliases",
        {"tag_id": str(alias.tag_id), "name": "Leaked", "slug": "leaked"},
        format="json",
    )

    assert list_response.status_code == 200
    assert list_response.json()["data"] == []
    assert detail_response.status_code == 404
    assert update_response.status_code == 404
    assert delete_response.status_code == 404
    assert cross_tenant_create.status_code == 400


def test_alias_detail_does_not_mask_database_errors(api_client, monkeypatch):
    alias = make_alias(slug="promoted")

    def broken_aliases_for_tenant(*_args, **_kwargs):
        raise OperationalError("database is unavailable")

    monkeypatch.setattr(
        "octonomy.tags.alias_views.aliases_for_tenant",
        broken_aliases_for_tenant,
    )

    with pytest.raises(OperationalError):
        api_client.get(f"/api/v1/tag-aliases/{alias.id}")


def test_alias_endpoints_enforce_scopes():
    token, _client = create_service_client_token(
        name="svc-read-only-aliases",
        grants=[
            {
                "tenant_id": "tenant_a",
                "application_id": None,
                "scopes": ["tags:read"],
            }
        ],
    )
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}", HTTP_X_TENANT_ID="tenant_a")
    tag = make_tag(slug="featured")

    read_response = client.get("/api/v1/tag-aliases")
    write_response = client.post(
        "/api/v1/tag-aliases",
        {"tag_id": str(tag.id), "name": "Hero", "slug": "hero"},
        format="json",
    )

    assert read_response.status_code == 200
    assert write_response.status_code == 403


def test_alias_query_params_validate_application_id(api_client):
    alias = make_alias(slug="promoted")

    list_response = api_client.get("/api/v1/tag-aliases?application_id=%20")
    tag_aliases_response = api_client.get(f"/api/v1/tags/{alias.tag_id}/aliases?application_id=%20")
    resolution_response = api_client.get("/api/v1/tag-resolution?slug=promoted&application_id=%20")

    assert list_response.status_code == 400
    assert tag_aliases_response.status_code == 400
    assert resolution_response.status_code == 400


def test_alias_boolean_query_params_use_standard_parsing(api_client):
    shared = make_alias(slug="shared")
    app_alias = make_alias(
        tag=make_tag(application_id="commerce", slug="sale"),
        application_id="commerce",
        slug="promo",
    )
    inactive = make_alias(slug="inactive", is_active=False)

    app_only = api_client.get("/api/v1/tag-aliases?application_id=commerce&include_shared=0")
    inactive_only = api_client.get("/api/v1/tag-aliases?is_active=0")

    assert app_only.status_code == 200
    assert [item["id"] for item in app_only.json()["data"]] == [str(app_alias.id)]
    assert inactive_only.status_code == 200
    assert [item["id"] for item in inactive_only.json()["data"]] == [str(inactive.id)]
    assert str(shared.id) not in [item["id"] for item in app_only.json()["data"]]


def test_create_alias_rejects_inactive_tag(api_client):
    tag = make_tag(slug="archived", is_active=False)

    response = api_client.post(
        "/api/v1/tag-aliases",
        {"tag_id": str(tag.id), "name": "Old", "slug": "old"},
        format="json",
    )

    assert response.status_code == 400
    assert response.json()["error"]["details"]["tag_id"] == ["Tag is inactive."]


def test_update_alias_rejects_inactive_tag(api_client):
    alias = make_alias(slug="hero")
    inactive_tag = make_tag(slug="archived", is_active=False)

    response = api_client.patch(
        f"/api/v1/tag-aliases/{alias.id}",
        {"tag_id": str(inactive_tag.id)},
        format="json",
    )

    assert response.status_code == 400
    assert response.json()["error"]["details"]["tag_id"] == ["Tag is inactive."]


def test_resolution_by_tag_and_alias_slug(api_client):
    featured = make_tag(slug="featured")
    make_alias(tag=featured, slug="promoted")

    tag_response = api_client.get("/api/v1/tag-resolution?slug=featured")
    alias_response = api_client.get("/api/v1/tag-resolution?slug=promoted")

    assert tag_response.status_code == 200
    assert tag_response.json()["data"]["matched_type"] == "tag"
    assert tag_response.json()["data"]["matched_alias"] is None
    assert alias_response.status_code == 200
    assert alias_response.json()["data"]["matched_type"] == "alias"
    assert alias_response.json()["data"]["tag"]["slug"] == "featured"


def test_resolution_without_application_only_returns_shared_canonical_tag(api_client):
    shared = make_tag(slug="dup")
    make_tag(application_id="commerce", slug="dup")

    response = api_client.get("/api/v1/tag-resolution?slug=dup")

    assert response.status_code == 200
    assert response.json()["data"]["tag"]["id"] == str(shared.id)


def test_resolution_requires_type_for_ambiguous_canonical_slug(api_client):
    make_tag(slug="featured", type="label")
    state = make_tag(slug="featured", type="state")

    ambiguous = api_client.get("/api/v1/tag-resolution?slug=featured")
    resolved = api_client.get("/api/v1/tag-resolution?slug=featured&type=state")

    assert ambiguous.status_code == 400
    assert ambiguous.json()["error"]["details"]["type"] == [
        "Multiple canonical tags match this slug; provide type."
    ]
    assert resolved.status_code == 200
    assert resolved.json()["data"]["tag"]["id"] == str(state.id)


def test_resolution_prefers_canonical_tag_then_app_alias(api_client):
    shared = make_tag(slug="featured")
    commerce = make_tag(application_id="commerce", slug="sale")
    make_alias(tag=shared, slug="hero")
    make_alias(tag=commerce, application_id="commerce", slug="hero")
    make_alias(tag=shared, slug="sale")

    alias_response = api_client.get("/api/v1/tag-resolution?slug=hero&application_id=commerce")
    canonical_response = api_client.get("/api/v1/tag-resolution?slug=sale&application_id=commerce")

    assert alias_response.json()["data"]["tag"]["slug"] == "sale"
    assert canonical_response.json()["data"]["matched_type"] == "tag"
    assert canonical_response.json()["data"]["tag"]["slug"] == "sale"


def test_inactive_alias_does_not_resolve(api_client):
    make_alias(slug="promoted", is_active=False)

    response = api_client.get("/api/v1/tag-resolution?slug=promoted")

    assert response.status_code == 400


def test_alias_of_inactive_tag_does_not_resolve(api_client):
    tag = make_tag(slug="archived")
    alias = make_alias(tag=tag, slug="legacy")

    tag.is_active = False
    tag.save(update_fields=["is_active", "updated_at"])
    response = api_client.get("/api/v1/tag-resolution?slug=legacy")

    assert alias.is_active is True
    assert response.status_code == 400


def test_deactivating_tag_deactivates_aliases(api_client):
    tag = make_tag(slug="featured")
    alias = make_alias(tag=tag, slug="hero")

    response = api_client.delete(f"/api/v1/tags/{tag.id}")
    alias.refresh_from_db()
    audit_log = AuditLog.objects.get(action="tag.deactivated")

    assert response.status_code == 204
    assert alias.is_active is False
    assert audit_log.changes["cascaded_alias_ids"] == [str(alias.id)]


def test_resolution_validates_slug_shape(api_client):
    response = api_client.get("/api/v1/tag-resolution?slug=Bad Slug")

    assert response.status_code == 400
    assert response.json()["error"]["details"]["slug"] == (
        "Use lowercase letters, numbers, underscores, or hyphens."
    )
