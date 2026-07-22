from __future__ import annotations

import time
import uuid
from dataclasses import dataclass

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, models, transaction

from octonomy.assignments.models import TagAssignment
from octonomy.audit.models import AuditLog
from octonomy.events.models import OutboxEvent
from octonomy.service_auth.models import ServiceClientGrant
from octonomy.tags.models import Tag, TagAlias, Vocabulary


@dataclass(frozen=True)
class SwapTable:
    """A table touched by the S1 namespace constraint-swap migrations.

    ``scan_ops`` is the number of full-table operations the swap performs on the
    table while holding a lock: plain index builds, the check-constraint
    validation, and partial-unique index builds. Lock time scales roughly with
    ``scan_ops * row_count``.
    """

    label: str
    model: type[models.Model]
    scan_ops: int
    hot: bool = False


# Op counts are read directly from the merged S1 swap migrations:
#   assignments/0002, audit/0002, events/0003, tags/0004, service_auth/0002.
SWAP_TABLES = (
    SwapTable("tag_assignments", TagAssignment, scan_ops=5, hot=True),
    SwapTable("audit_logs", AuditLog, scan_ops=3, hot=True),
    SwapTable("outbox_events", OutboxEvent, scan_ops=3, hot=True),
    SwapTable("tags", Tag, scan_ops=6),
    SwapTable("tag_aliases", TagAlias, scan_ops=6),
    SwapTable("vocabularies", Vocabulary, scan_ops=6),
    SwapTable("service_client_grants", ServiceClientGrant, scan_ops=9),
)


def per_million_seconds(wall_seconds: float, rows: int) -> float:
    """Seconds of table-lock per 1M rows. Pure; unit-tested without a database."""
    return wall_seconds / (rows / 1_000_000) if rows else 0.0


class Command(BaseCommand):
    help = (
        "Estimate the maintenance window for the S1 namespace constraint-swap "
        "migrations (NS-6). Reports row counts/sizes for the affected tables and, "
        "with --rehearse, times the swap operations on a synthetic PostgreSQL table."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--rehearse",
            action="store_true",
            help=(
                "Build a throwaway synthetic table at --rows scale, time the swap "
                "operations against it, then drop it (PostgreSQL only, non-destructive)."
            ),
        )
        parser.add_argument(
            "--rows",
            type=int,
            default=1_000_000,
            help="Synthetic row count for --rehearse (default: 1,000,000).",
        )

    def handle(self, *args, **options):
        vendor = connection.vendor
        self.stdout.write("namespace swap lock estimate")
        self.stdout.write(f"engine={vendor}")

        self.report_counts(vendor)

        if options["rehearse"]:
            if vendor != "postgresql":
                raise CommandError(
                    "--rehearse requires PostgreSQL (lock behaviour is engine-specific); "
                    f"current engine is {vendor}. Run it against a restored prod clone."
                )
            if options["rows"] <= 0:
                raise CommandError("--rows must be a positive integer.")
            self.rehearse(rows=options["rows"])
        else:
            self.stdout.write(
                "hint: run with --rehearse --rows <prod-sized N> on a restored prod "
                "clone to measure the lock window."
            )

    def report_counts(self, vendor: str) -> None:
        for spec in SWAP_TABLES:
            table = spec.model._meta.db_table
            rows = spec.model.objects.count()
            parts = [f"table={spec.label}", f"rows={rows}", f"scan_ops={spec.scan_ops}"]
            if vendor == "postgresql":
                parts.append(f"size={self.table_size(table)}")
            if spec.hot:
                parts.append("hot=yes")
            self.stdout.write(" ".join(parts))

    def table_size(self, table: str) -> str:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_size_pretty(pg_total_relation_size(%s))", [table])
            return cursor.fetchone()[0].replace(" ", "")

    def rehearse(self, rows: int) -> None:
        # A permanent throwaway table (not TEMPORARY): temp relations skip WAL and
        # use local buffers, so their index-build timing understates a real permanent
        # table -- the wrong direction for a maintenance-window estimate. The uuid
        # name makes collision with a real table effectively impossible, and `created`
        # guards cleanup so a failed CREATE never drops a same-named real table.
        shadow = f"_ns_swap_rehearsal_{uuid.uuid4().hex}"
        self.stdout.write(f"rehearsal rows={rows} shadow={shadow}")
        created = False
        try:
            self._create_shadow(shadow)
            created = True
            self._populate_shadow(shadow, rows)
            results, wall = self._time_swap_ops(shadow)
        finally:
            if created:
                self._drop_shadow(shadow)

        for name, lock, seconds in results:
            self.stdout.write(f"op={name} lock={lock} seconds={seconds:.3f}")

        # The real migration runs in ONE transaction whose first statement drops
        # the old unique constraint (ACCESS EXCLUSIVE, held to commit), so the whole
        # window blocks reads AND writes -- the per-op SHARE locks above never open a
        # read window in practice. table_locked_seconds is the end-to-end window.
        per_million = per_million_seconds(wall, rows)
        self.stdout.write(
            f"table_locked_seconds={wall:.3f} "
            "(reads+writes; whole swap runs under ACCESS EXCLUSIVE held to commit)"
        )
        self.stdout.write(
            f"assignments_swap=~{per_million:.1f}s per 1M rows on this host "
            "(dominated by index-build sort; tune maintenance_work_mem to reduce it)."
        )
        self.stdout.write(
            "audit_logs/outbox_events are lighter at equal row count (2 plain indexes + "
            "cheap check, no unique-index sort); treat the assignments figure as an "
            "upper bound. Re-run on a prod clone at real row counts."
        )

    def _create_shadow(self, shadow: str) -> None:
        # Mirror the PRE-swap tag_assignments shape (UUID PK, assigned_by, the old
        # plain unique constraint) WITHOUT the namespace columns -- those are added by
        # the timed swap below, exactly as assignments/0002 does. shadow is a
        # fixed-charset generated name, so the interpolated identifier is injection-safe.
        with connection.cursor() as cursor:
            cursor.execute(
                f'CREATE TABLE "{shadow}" ('
                "  id uuid PRIMARY KEY,"
                "  tenant_id varchar(100) NOT NULL,"
                "  application_id varchar(100) NOT NULL,"
                "  resource_type varchar(100) NOT NULL,"
                "  resource_id varchar(255) NOT NULL,"
                "  tag_id uuid NOT NULL,"
                "  assigned_by varchar(255),"
                "  assigned_at timestamptz NOT NULL DEFAULT now()"
                ")"
            )

    def _populate_shadow(self, shadow: str, rows: int) -> None:
        # All rows global (namespace absent), which is the real state at swap time.
        with connection.cursor() as cursor:
            cursor.execute(
                f'INSERT INTO "{shadow}" '
                "  (id, tenant_id, application_id, resource_type, resource_id, tag_id) "
                "SELECT gen_random_uuid(), 'tenant_' || (g %% 50), 'commerce', "
                "       'product', 'res_' || g, gen_random_uuid() "
                "FROM generate_series(1, %s) AS g",
                [rows],
            )
            # The pre-swap plain unique constraint the migration drops first.
            cursor.execute(
                f'ALTER TABLE "{shadow}" ADD CONSTRAINT "{shadow}_uniq_old" '
                "UNIQUE (tenant_id, application_id, resource_type, resource_id, tag_id)"
            )
            cursor.execute(f'ANALYZE "{shadow}"')

    def _time_swap_ops(self, shadow: str) -> tuple[list[tuple[str, str, float]], float]:
        # Mirrors all eight assignments/0002 operations, in order: drop the old unique
        # constraint, add the two namespace columns, build the plain indexes, add the
        # check, then build the partial-unique indexes -- all in one transaction,
        # exactly as the atomic migration does.
        ns_scope = (
            "((namespace_id IS NULL AND namespace_type IS NULL) OR "
            "(application_id IS NOT NULL AND namespace_id IS NOT NULL AND "
            "namespace_type IS NOT NULL AND namespace_type <> '' AND "
            "namespace_id <> '' AND namespace_type <> 'global'))"
        )
        ops = [
            (
                "drop_old_unique:uniq_assignment_per_resource_tag",
                "ACCESS EXCLUSIVE",
                f'ALTER TABLE "{shadow}" DROP CONSTRAINT "{shadow}_uniq_old"',
            ),
            (
                "add_column:namespace_id",
                "ACCESS EXCLUSIVE",
                f'ALTER TABLE "{shadow}" ADD COLUMN namespace_id varchar(100)',
            ),
            (
                "add_column:namespace_type",
                "ACCESS EXCLUSIVE",
                f'ALTER TABLE "{shadow}" ADD COLUMN namespace_type varchar(100)',
            ),
            (
                "create_index:assign_scope_resource_idx",
                "SHARE",
                f'CREATE INDEX "{shadow}_scope_resource" ON "{shadow}" '
                "(tenant_id, application_id, namespace_type, namespace_id, "
                "resource_type, resource_id)",
            ),
            (
                "create_index:assign_tenant_tag_ns_idx",
                "SHARE",
                f'CREATE INDEX "{shadow}_tenant_tag_ns" ON "{shadow}" '
                "(tenant_id, tag_id, namespace_type, namespace_id)",
            ),
            (
                "add_check:namespace_scope",
                "ACCESS EXCLUSIVE",
                f'ALTER TABLE "{shadow}" ADD CONSTRAINT "{shadow}_ns_scope" CHECK {ns_scope}',
            ),
            (
                "create_unique_index:uniq_global_assignment_tag",
                "SHARE",
                f'CREATE UNIQUE INDEX "{shadow}_uniq_global" ON "{shadow}" '
                "(tenant_id, application_id, resource_type, resource_id, tag_id) "
                "WHERE namespace_id IS NULL AND namespace_type IS NULL",
            ),
            (
                "create_unique_index:uniq_ns_assignment_tag",
                "SHARE",
                f'CREATE UNIQUE INDEX "{shadow}_uniq_ns" ON "{shadow}" '
                "(tenant_id, application_id, namespace_type, namespace_id, "
                "resource_type, resource_id, tag_id) "
                "WHERE namespace_id IS NOT NULL AND namespace_type IS NOT NULL",
            ),
        ]
        results: list[tuple[str, str, float]] = []
        wall_start = time.monotonic()
        with transaction.atomic(), connection.cursor() as cursor:
            for name, lock, sql in ops:
                op_start = time.monotonic()
                cursor.execute(sql)
                results.append((name, lock, time.monotonic() - op_start))
        wall = time.monotonic() - wall_start
        return results, wall

    def _drop_shadow(self, shadow: str) -> None:
        with connection.cursor() as cursor:
            cursor.execute(f'DROP TABLE IF EXISTS "{shadow}"')
