"""Atomic reactivate_* services (#85 review fix).

Reactivation is a single service-owned operation that re-validates active relations
under row locks inside the write transaction, so it cannot revive a row into an invalid
state and it honors active-only slug uniqueness.
"""

from __future__ import annotations

import pytest

from octonomy.core.errors import ConflictError, DomainError
from octonomy.tags.alias_services import reactivate_tag_alias
from octonomy.tags.services import reactivate_tag
from octonomy.tags.vocabulary_services import reactivate_vocabulary
from tests.factories import make_alias, make_tag, make_vocabulary

pytestmark = pytest.mark.django_db


def test_reactivate_tag_revives_and_is_idempotent():
    tag = make_tag(slug="featured", is_active=False)

    reactivate_tag(tag)
    tag.refresh_from_db()
    assert tag.is_active is True

    # Reactivating an already-active tag is a no-op (no error).
    reactivate_tag(tag)
    tag.refresh_from_db()
    assert tag.is_active is True


def test_reactivate_tag_rejects_inactive_vocabulary():
    vocab = make_vocabulary(slug="labels", is_active=False)
    tag = make_tag(slug="scoped", is_active=False, vocabulary=vocab)

    with pytest.raises(DomainError):
        reactivate_tag(tag)

    tag.refresh_from_db()
    assert tag.is_active is False


def test_reactivate_tag_conflicts_with_active_slug():
    inactive = make_tag(slug="featured", type="label", is_active=False)
    make_tag(slug="featured", type="label", is_active=True)  # occupies the active slug

    with pytest.raises(ConflictError):
        reactivate_tag(inactive)

    inactive.refresh_from_db()
    assert inactive.is_active is False


def test_reactivate_vocabulary_conflicts_with_active_slug():
    inactive = make_vocabulary(slug="labels", is_active=False)
    make_vocabulary(slug="labels", is_active=True)

    with pytest.raises(ConflictError):
        reactivate_vocabulary(inactive)

    inactive.refresh_from_db()
    assert inactive.is_active is False


def test_reactivate_alias_rejects_inactive_tag():
    tag = make_tag(slug="canonical", is_active=False)
    alias = make_alias(tag=tag, slug="alt", is_active=False)

    with pytest.raises(DomainError):
        reactivate_tag_alias(alias)

    alias.refresh_from_db()
    assert alias.is_active is False


def test_reactivate_alias_revives_when_tag_active():
    tag = make_tag(slug="canonical", is_active=True)
    alias = make_alias(tag=tag, slug="alt", is_active=False)

    reactivate_tag_alias(alias)
    alias.refresh_from_db()
    assert alias.is_active is True
