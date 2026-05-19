from __future__ import annotations

import pytest

from octonomy.core.errors import ConflictError, DomainError
from octonomy.tags.services import create_tag
from tests.factories import make_tag

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
