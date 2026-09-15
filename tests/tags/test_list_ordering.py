"""GET /tags emits a total ORDER BY, so paging cannot repeat or skip rows (issue #162).

`usage_count` is a `Count` annotation, which makes the tags list an aggregate query, and
Django has not applied `Meta.ordering` to aggregate queries since 3.1. The endpoint
therefore emitted no `ORDER BY` at all, and LIMIT/OFFSET over an unordered query is
undefined in SQL: two pages could deliver one row twice and silently drop another with no
concurrent writes involved, purely because the planner switched between HashAggregate and
GroupAggregate between two requests.

These tests pin an exact expected sequence rather than comparing one walk against another.
A self-comparison passes against the broken selector whenever the planner stays consistent
within a single process, which is why the pre-existing v2 stability test never caught this.
The fixture is built so that name order, slug order, id order and insertion order all
disagree — if any two of them agreed, a test could pass on the wrong ordering.

Names and slugs are lowercase ASCII throughout, so the expected sequence is identical under
PostgreSQL's collation and SQLite's binary comparison.
"""

from __future__ import annotations

import re
import uuid

import pytest

from octonomy.tags.selectors import tags_for_tenant
from tests.factories import make_tag

pytestmark = pytest.mark.django_db


def pinned_id(tail: str) -> uuid.UUID:
    return uuid.UUID(f"00000000-0000-4000-8000-00000000{tail}")


# (id tail, name, slug), created in this order.
ORDERING_ROWS = (
    ("0002", "cherry", "alpha"),
    ("0003", "apple", "zulu"),
    ("0001", "banana", "mike"),
)

# name ASC, slug ASC, id ASC — which is none of insertion, slug or id order.
EXPECTED_SLUGS = ["zulu", "mike", "alpha"]


@pytest.fixture
def ordering_rows(db):
    return [
        make_tag(id=pinned_id(tail), name=name, slug=slug) for tail, name, slug in ORDERING_ROWS
    ]


def slugs_of(response) -> list[str]:
    assert response.status_code == 200, response.data
    return [item["slug"] for item in response.json()["data"]]


def test_ordering_fixture_discriminates():
    # Guards the fixture itself: the expected sequence has to differ from every other
    # order the endpoint could plausibly return, or a green test proves nothing.
    insertion = [slug for _tail, _name, slug in ORDERING_ROWS]
    by_slug = sorted(insertion)
    by_id = [slug for _tail, _name, slug in sorted(ORDERING_ROWS)]
    assert EXPECTED_SLUGS not in (insertion, by_slug, by_id)
    assert len({tuple(insertion), tuple(by_slug), tuple(by_id)}) == 3


def test_selector_emits_the_full_ordering_chain():
    # The direct regression assertion: this is False and the ORDER BY is absent on the
    # pre-fix selector, because Meta.ordering is dropped by the GROUP BY.
    queryset = tags_for_tenant("tenant_a")

    assert queryset.ordered is True
    sql = str(queryset.query)
    assert sql.index("GROUP BY") < sql.index("ORDER BY")
    order_by = sql[sql.index("ORDER BY") :]
    assert re.findall(r'"tags"\."(\w+)" ASC', order_by) == ["name", "slug", "id"]


def test_list_returns_rows_in_the_declared_order(api_client, ordering_rows):
    assert slugs_of(api_client.get("/api/v1/tags")) == EXPECTED_SLUGS


def test_paging_one_row_at_a_time_loses_nothing(api_client, ordering_rows):
    seen, offset = [], 0
    while True:
        response = api_client.get(f"/api/v1/tags?limit=1&offset={offset}")
        assert response.status_code == 200, response.data
        payload = response.json()
        seen.extend(item["slug"] for item in payload["data"])
        offset += 1
        if offset >= payload["pagination"]["count"]:
            break

    assert seen == EXPECTED_SLUGS


def test_v2_list_is_ordered_too(api_client, ordering_rows):
    # config/urls.py routes one api/<version>/ path into the same view, so both API
    # versions carried the bug and both have to be pinned.
    assert slugs_of(api_client.get("/api/v2/tags")) == EXPECTED_SLUGS


def test_equal_names_tiebreak_on_slug_then_id(api_client):
    # Active-slug uniqueness is keyed on (tenant, type, slug), so two rows may share a
    # name *and* a slug when their types differ — the case where only id separates them.
    bravo = make_tag(id=pinned_id("0009"), name="dup", slug="bravo", type="label")
    alpha_late = make_tag(id=pinned_id("0008"), name="dup", slug="alpha", type="label")
    alpha_early = make_tag(id=pinned_id("0001"), name="dup", slug="alpha", type="category")

    response = api_client.get("/api/v1/tags")

    assert response.status_code == 200, response.data
    assert [item["id"] for item in response.json()["data"]] == [
        str(alpha_early.id),
        str(alpha_late.id),
        str(bravo.id),
    ]
