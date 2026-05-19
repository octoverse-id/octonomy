from __future__ import annotations

import pytest

from octonomy.assignments.models import TagAssignment
from tests.factories import make_tag

pytestmark = pytest.mark.django_db


def test_delete_assignment_is_idempotent(api_client):
    tag = make_tag(slug="featured")
    payload = {
        "application_id": "commerce",
        "tag_id": str(tag.id),
        "resource_type": "product",
        "resource_id": "prod_123",
    }
    api_client.post("/api/v1/tag-assignments", payload, format="json")

    first = api_client.delete("/api/v1/tag-assignments", payload, format="json")
    second = api_client.delete("/api/v1/tag-assignments", payload, format="json")

    assert first.status_code == 204
    assert second.status_code == 204
    assert TagAssignment.objects.count() == 0
