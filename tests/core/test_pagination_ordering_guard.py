"""OctonomyLimitOffsetPagination re-arms Django's unordered-pagination detector (#162).

django.core.paginator.Paginator raises UnorderedObjectListWarning for exactly the hazard
that shipped in GET /tags, but DRF's LimitOffsetPagination never builds a Paginator — it
slices the queryset directly — so that warning could not fire anywhere in this codebase.
All six paginate_queryset call sites go through OctonomyLimitOffsetPagination, so the check
belongs there. pyproject.toml promotes the warning to an error under pytest.
"""

from __future__ import annotations

import warnings

import pytest
from django.core.paginator import UnorderedObjectListWarning
from django.db.models import Count
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from octonomy.assignments.models import TagAssignment
from octonomy.core.pagination import OctonomyLimitOffsetPagination
from octonomy.tags.models import Tag
from octonomy.tags.selectors import tags_for_tenant
from tests.factories import make_alias, make_tag, make_vocabulary

pytestmark = pytest.mark.django_db


def paginate(queryset):
    request = Request(APIRequestFactory().get("/api/v1/tags"))
    return OctonomyLimitOffsetPagination().paginate_queryset(queryset, request)


def test_unordered_queryset_warns():
    make_tag(slug="a")
    # The pre-fix shape of the tags list: an aggregate, so Meta.ordering is dropped.
    queryset = Tag.objects.annotate(usage_count=Count("assignments"))
    assert queryset.ordered is False

    with pytest.warns(UnorderedObjectListWarning, match="unordered object_list"):
        paginate(queryset)


def test_ordered_queryset_does_not_warn():
    make_tag(slug="a")

    with warnings.catch_warnings():
        warnings.simplefilter("error", UnorderedObjectListWarning)
        assert len(paginate(tags_for_tenant("tenant_a"))) == 1


def test_every_paginated_list_endpoint_is_ordered(api_client):
    # End-to-end net over all six call sites: with the pyproject filter promoting
    # UnorderedObjectListWarning to an error, an unordered list view raises inside the
    # view and the test client re-raises it, so a 200 here is the assertion.
    tag = make_tag(slug="ordered")
    make_alias(tag=tag, slug="ordered-alias")
    make_vocabulary(slug="ordered-vocab")
    TagAssignment.objects.create(
        tenant_id="tenant_a",
        application_id="commerce",
        tag=tag,
        resource_type="product",
        resource_id="p1",
    )

    for path in (
        "/api/v1/tags",
        "/api/v1/tag-aliases",
        "/api/v1/vocabularies",
        "/api/v1/audit-logs",
        f"/api/v1/tags/{tag.id}/aliases",
        f"/api/v1/tags/{tag.id}/resources",
        "/api/v1/resources/product/p1/tags?application_id=commerce",
    ):
        response = api_client.get(path)
        assert response.status_code == 200, (path, response.data)
