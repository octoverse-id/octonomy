"""Concurrency + reactivation conflicts (issue #44, eng review D33).

The S1 constraint swap replaced the old single active-slug constraints with the
three post-swap partial-unique constraints (``uniq_app_ns_tag_slug`` and kin).
These tests pin the behaviour those constraints must produce:

- a same-slug create race inside one namespace resolves to a 409 conflict
  envelope, while the identical slug in a *sibling* namespace does not conflict
  (isolation, not a global slug lock);
- the database — not an app-layer pre-check — is the race arbiter, so two writers
  that both pass validation still cannot both land;
- the deactivate → recreate → reactivate matrix has a *defined* conflict rather
  than silently producing two active rows in one scope.
"""

from __future__ import annotations

from unittest import mock

import pytest
from django.db import IntegrityError, transaction
from django.test import override_settings

from octonomy.tags.models import Tag
from tests.factories import make_tag
from tests.isolation.registry import APP

pytestmark = pytest.mark.django_db

NS_A = {"namespace_type": "merchant", "namespace_id": "merchant_a"}
NS_B = {"namespace_type": "merchant", "namespace_id": "merchant_b"}


def _create_tag(client, slug: str):
    return client.post(
        "/api/v2/tags",
        {"application_id": APP, "name": slug, "slug": slug, "type": "label"},
        format="json",
    )


@override_settings(NAMESPACE_WRITE_ENABLED=True)
def test_same_slug_namespaced_creates_conflict_with_409_envelope(merchant_a_client):
    first = _create_tag(merchant_a_client, "raced")
    assert first.status_code == 201, first.data

    second = _create_tag(merchant_a_client, "raced")
    assert second.status_code == 409, second.data
    error = second.json()["error"]
    assert error["code"] == "conflict"
    assert "slug" in error["details"]
    # The loser of the race left no second active row behind.
    assert Tag.objects.filter(slug="raced", **NS_A, is_active=True).count() == 1


@override_settings(NAMESPACE_WRITE_ENABLED=True)
def test_same_slug_in_a_sibling_namespace_does_not_conflict(merchant_a_client, merchant_b_client):
    # The post-swap constraint keys on (application, namespace, type, slug), so the
    # same slug is free in another merchant's namespace — a conflict here would be
    # a cross-namespace slug lock, i.e. an isolation break.
    a = _create_tag(merchant_a_client, "coexist")
    b = _create_tag(merchant_b_client, "coexist")
    assert a.status_code == 201, a.data
    assert b.status_code == 201, b.data
    assert a.json()["data"]["id"] != b.json()["data"]["id"]


@override_settings(NAMESPACE_WRITE_ENABLED=True)
def test_insert_time_integrity_error_is_translated_to_409(merchant_a_client):
    # The true race: our request clears every app-layer pre-check, then a
    # concurrent writer wins the unique constraint so *our* INSERT raises
    # IntegrityError. The view must surface the 409 conflict envelope, never a
    # 500. Patching the create call reproduces the losing writer deterministically.
    with mock.patch(
        "octonomy.tags.services.Tag.objects.create",
        side_effect=IntegrityError("duplicate key"),
    ):
        response = _create_tag(merchant_a_client, "racemock")

    assert response.status_code == 409, response.data
    assert response.json()["error"]["code"] == "conflict"


def test_database_constraint_is_the_race_arbiter(db):
    # Simulate two writers that both cleared any app-layer pre-check and race to
    # INSERT. The database — not application code — must serialise them, which is
    # what makes the HTTP 409 above safe under real concurrency.
    make_tag(application_id=APP, slug="dbrace", **NS_A)
    with pytest.raises(IntegrityError), transaction.atomic():
        make_tag(application_id=APP, slug="dbrace", **NS_A)


def test_reactivation_matrix_has_a_defined_conflict(db):
    # deactivate T1 -> recreate the slug (T2) -> attempt to reactivate T1.
    first = make_tag(application_id=APP, slug="reactivated", **NS_A)

    first.is_active = False
    first.save(update_fields=["is_active"])

    # The slug is free again while T1 is inactive: the active-only constraint no
    # longer covers T1.
    second = make_tag(application_id=APP, slug="reactivated", **NS_A)
    assert second.is_active

    # Reactivating T1 would create two active rows in one scope — the matrix's
    # defined outcome is a conflict, never a silent duplicate.
    first.is_active = True
    with pytest.raises(IntegrityError), transaction.atomic():
        first.save(update_fields=["is_active"])


def test_reactivation_is_allowed_once_the_conflicting_row_is_gone(db):
    # The conflict is specifically about *co-active* rows: with T2 deactivated,
    # reactivating the original must succeed, so the matrix is not a dead end.
    first = make_tag(application_id=APP, slug="reactivable", **NS_A)
    first.is_active = False
    first.save(update_fields=["is_active"])

    second = make_tag(application_id=APP, slug="reactivable", **NS_A)
    second.is_active = False
    second.save(update_fields=["is_active"])

    first.is_active = True
    first.save(update_fields=["is_active"])
    assert Tag.objects.filter(slug="reactivable", **NS_A, is_active=True).count() == 1
