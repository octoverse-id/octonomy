from __future__ import annotations

from types import SimpleNamespace

import pytest

from octonomy.core.errors import ScopeImmutableError
from octonomy.core.selectors import SCOPE_FIELDS, guard_scope_immutable, scope_changed_fields


def _row(application_id="commerce", namespace_type=None, namespace_id=None):
    return SimpleNamespace(
        application_id=application_id,
        namespace_type=namespace_type,
        namespace_id=namespace_id,
    )


def test_scope_fields_are_the_three_isolation_axes():
    assert SCOPE_FIELDS == ("application_id", "namespace_type", "namespace_id")


def test_guard_allows_non_scope_changes():
    # Non-scope fields and scope fields set to their current value are fine.
    guard_scope_immutable(_row(), {"name": "Renamed", "slug": "renamed"})
    guard_scope_immutable(_row(), {"application_id": "commerce"})
    guard_scope_immutable(_row(), {})


@pytest.mark.parametrize(
    "row, data, expected",
    [
        (_row(), {"application_id": "cms"}, ["application_id"]),
        (
            _row(namespace_type="merchant", namespace_id="a"),
            {"namespace_type": "reseller"},
            ["namespace_type"],
        ),
        (
            _row(namespace_type="merchant", namespace_id="a"),
            {"namespace_id": "b"},
            ["namespace_id"],
        ),
        (
            _row(namespace_type="merchant", namespace_id="a"),
            {"application_id": "cms", "namespace_id": "b"},
            ["application_id", "namespace_id"],
        ),
    ],
)
def test_scope_changed_fields_reports_exactly_the_moved_fields(row, data, expected):
    assert scope_changed_fields(row, data) == expected


@pytest.mark.parametrize(
    "row, data, changed",
    [
        (_row(), {"application_id": "cms"}, "application_id"),
        (
            _row(namespace_type="merchant", namespace_id="a"),
            {"namespace_type": "reseller"},
            "namespace_type",
        ),
        (
            _row(namespace_type="merchant", namespace_id="a"),
            {"namespace_id": "b"},
            "namespace_id",
        ),
    ],
)
def test_guard_rejects_each_scope_move(row, data, changed):
    with pytest.raises(ScopeImmutableError) as exc_info:
        guard_scope_immutable(row, data)
    error = exc_info.value
    assert error.code == "scope_immutable"
    assert error.status_code == 409
    assert changed in error.details


def test_guard_reports_all_moved_scope_fields():
    row = _row(namespace_type="merchant", namespace_id="a")
    with pytest.raises(ScopeImmutableError) as exc_info:
        guard_scope_immutable(row, {"application_id": "cms", "namespace_id": "b"})
    assert set(exc_info.value.details) == {"application_id", "namespace_id"}
