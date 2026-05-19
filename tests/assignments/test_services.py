from __future__ import annotations

import pytest

from octonomy.assignments.services import assign_tag
from octonomy.core.errors import ApplicationMismatchError, InactiveTagError
from tests.factories import make_tag

pytestmark = pytest.mark.django_db


def test_inactive_tag_cannot_be_assigned():
    tag = make_tag(slug="archived", is_active=False)

    with pytest.raises(InactiveTagError):
        assign_tag("tenant_a", "commerce", tag, "product", "prod_123")


def test_application_mismatch_rejected():
    tag = make_tag(application_id="commerce", slug="sale")

    with pytest.raises(ApplicationMismatchError):
        assign_tag("tenant_a", "cms", tag, "article", "article_123")
