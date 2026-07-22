from __future__ import annotations

import re
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection

from octonomy.core.management.commands.estimate_namespace_swap_lock import (
    per_million_seconds,
)


def _run_counts() -> str:
    out = StringIO()
    call_command("estimate_namespace_swap_lock", stdout=out)
    return out.getvalue()


def _line_for(output: str, table: str) -> str:
    prefix = f"table={table} "
    for line in output.splitlines():
        if line.startswith(prefix):
            return line
    raise AssertionError(f"no line for {table!r} in:\n{output}")


def test_per_million_seconds_scales_linearly():
    assert per_million_seconds(35.0, 1_000_000) == pytest.approx(35.0)
    assert per_million_seconds(5.0, 200_000) == pytest.approx(25.0)


def test_per_million_seconds_handles_zero_rows():
    assert per_million_seconds(1.0, 0) == 0.0


@pytest.mark.django_db
def test_counts_mode_reports_every_affected_table_with_scan_ops():
    output = _run_counts()

    assert "namespace swap lock estimate" in output
    # The three high-volume append tables NS-6 names are flagged hot.
    for table, scan_ops in (("tag_assignments", 5), ("audit_logs", 3), ("outbox_events", 3)):
        line = _line_for(output, table)
        assert f"scan_ops={scan_ops}" in line
        assert "hot=yes" in line
    # Metadata tables are reported but not flagged hot.
    for table, scan_ops in (
        ("tags", 6),
        ("tag_aliases", 6),
        ("vocabularies", 6),
        ("service_client_grants", 9),
    ):
        line = _line_for(output, table)
        assert f"scan_ops={scan_ops}" in line
        assert "hot=yes" not in line


@pytest.mark.django_db
def test_counts_mode_prints_rehearse_hint():
    assert "hint: run with --rehearse" in _run_counts()


@pytest.mark.django_db
def test_rehearse_rejects_non_postgresql():
    if connection.vendor == "postgresql":
        pytest.skip("rehearse is supported on PostgreSQL")
    with pytest.raises(CommandError, match="requires PostgreSQL"):
        call_command("estimate_namespace_swap_lock", rehearse=True, rows=10)


@pytest.mark.django_db
def test_rehearse_times_all_swap_ops_and_cleans_up_on_postgresql():
    if connection.vendor != "postgresql":
        pytest.skip("rehearse requires PostgreSQL")

    out = StringIO()
    call_command("estimate_namespace_swap_lock", rehearse=True, rows=500, stdout=out)
    output = out.getvalue()

    assert "rehearsal rows=500" in output
    # All eight swap operations are timed, in order, starting with the drop that
    # takes ACCESS EXCLUSIVE for the whole transaction.
    for op in (
        "op=drop_old_unique:uniq_assignment_per_resource_tag lock=ACCESS EXCLUSIVE",
        "op=add_column:namespace_id lock=ACCESS EXCLUSIVE",
        "op=add_column:namespace_type lock=ACCESS EXCLUSIVE",
        "op=create_index:assign_scope_resource_idx lock=SHARE",
        "op=create_index:assign_tenant_tag_ns_idx lock=SHARE",
        "op=add_check:namespace_scope lock=ACCESS EXCLUSIVE",
        "op=create_unique_index:uniq_global_assignment_tag lock=SHARE",
        "op=create_unique_index:uniq_ns_assignment_tag lock=SHARE",
    ):
        assert op in output
    assert "table_locked_seconds=" in output
    assert "reads+writes" in output

    # The uniquely-named throwaway table is dropped after the rehearsal.
    match = re.search(r"shadow=(\S+)", output)
    assert match, output
    with connection.cursor() as cursor:
        cursor.execute("SELECT to_regclass(%s)", [match.group(1)])
        assert cursor.fetchone()[0] is None


@pytest.mark.django_db
def test_rehearse_rejects_nonpositive_rows_on_postgresql():
    if connection.vendor != "postgresql":
        pytest.skip("rehearse requires PostgreSQL")
    with pytest.raises(CommandError, match="--rows must be a positive integer"):
        call_command("estimate_namespace_swap_lock", rehearse=True, rows=0)
