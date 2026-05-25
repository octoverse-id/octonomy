from __future__ import annotations

import pytest

from octonomy.core.errors import ConflictError, DomainError
from octonomy.tags.alias_services import create_tag_alias
from octonomy.tags.models import Tag
from octonomy.tags.services import create_tag, deactivate_tag
from tests.factories import make_alias, make_tag

pytestmark = pytest.mark.django_db


def test_shared_tag_cannot_use_app_specific_parent():
    parent = make_tag(application_id="commerce", slug="commerce-parent")

    with pytest.raises(DomainError):
        create_tag(
            "tenant_a",
            {
                "application_id": None,
                "name": "Shared Child",
                "slug": "shared-child",
                "type": "label",
                "parent": parent,
                "metadata": {},
                "is_active": True,
            },
        )


def test_same_slug_allowed_after_deactivation():
    tag = make_tag(slug="featured")
    tag.is_active = False
    tag.save(update_fields=["is_active", "updated_at"])

    created = create_tag(
        "tenant_a",
        {
            "application_id": None,
            "name": "Featured",
            "slug": "featured",
            "type": "label",
            "metadata": {},
            "is_active": True,
        },
    )

    assert created.id != tag.id


def test_duplicate_active_tag_raises_conflict():
    make_tag(slug="featured")

    with pytest.raises(ConflictError):
        create_tag(
            "tenant_a",
            {
                "application_id": None,
                "name": "Featured",
                "slug": "featured",
                "type": "label",
                "metadata": {},
                "is_active": True,
            },
        )


def test_deactivate_tag_locks_tag_before_cascading_aliases(monkeypatch):
    tag = make_tag(slug="featured")
    alias = make_alias(tag=tag, slug="promoted")
    original_select_for_update = Tag.objects.select_for_update
    calls = []

    def spy_select_for_update(*args, **kwargs):
        calls.append(True)
        return original_select_for_update(*args, **kwargs)

    monkeypatch.setattr(Tag.objects, "select_for_update", spy_select_for_update)

    assert deactivate_tag(tag) is True

    alias.refresh_from_db()
    assert calls == [True]
    assert tag.is_active is False
    assert alias.is_active is False


def test_create_alias_locks_tag_before_validation(monkeypatch):
    tag = make_tag(slug="featured")
    original_select_for_update = Tag.objects.select_for_update
    calls = []

    def spy_select_for_update(*args, **kwargs):
        calls.append(True)
        return original_select_for_update(*args, **kwargs)

    monkeypatch.setattr(Tag.objects, "select_for_update", spy_select_for_update)

    alias = create_tag_alias(
        "tenant_a",
        {
            "application_id": None,
            "tag": tag,
            "name": "Promoted",
            "slug": "promoted",
            "metadata": {},
            "is_active": True,
        },
    )

    assert calls == [True]
    assert alias.tag_id == tag.id
