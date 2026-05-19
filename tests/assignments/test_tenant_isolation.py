from __future__ import annotations

import pytest

from octonomy.assignments.models import TagAssignment
from tests.factories import make_tag

pytestmark = pytest.mark.django_db


def test_tenant_cannot_assign_other_tenant_tag(api_client):
    tag = make_tag(tenant_id="tenant_b", slug="featured")

    response = api_client.post(
        "/api/v1/tag-assignments",
        {
            "application_id": "commerce",
            "tag_id": str(tag.id),
            "resource_type": "product",
            "resource_id": "prod_123",
        },
        format="json",
    )

    assert response.status_code == 400
    assert response.json()["error"]["details"]["tag_id"] == ["Tag was not found."]
    assert TagAssignment.objects.count() == 0
