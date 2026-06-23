from __future__ import annotations

from dataclasses import dataclass

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, models
from django.db.models import Q

from octonomy.assignments.models import TagAssignment
from octonomy.audit.models import AuditLog
from octonomy.core.models import namespace_scope_check
from octonomy.events.models import OutboxEvent
from octonomy.service_auth.models import ServiceClientGrant
from octonomy.tags.models import Tag, TagAlias, Vocabulary


@dataclass(frozen=True)
class NamespaceScopeSpec:
    label: str
    model: type[models.Model]
    has_wildcard: bool = False


NAMESPACE_SCOPE_SPECS = (
    NamespaceScopeSpec("tags", Tag),
    NamespaceScopeSpec("vocabularies", Vocabulary),
    NamespaceScopeSpec("tag_aliases", TagAlias),
    NamespaceScopeSpec("tag_assignments", TagAssignment),
    NamespaceScopeSpec("audit_logs", AuditLog),
    NamespaceScopeSpec("outbox_events", OutboxEvent),
    NamespaceScopeSpec("service_client_grants", ServiceClientGrant, has_wildcard=True),
)


class Command(BaseCommand):
    help = "Verify namespace scope invariants and print global/namespaced row counts."

    def handle(self, *args, **options):
        total_violations = 0
        self.stdout.write("namespace scope verification")

        for spec in NAMESPACE_SCOPE_SPECS:
            self.ensure_namespace_schema(spec)
            global_filter = Q(namespace_type__isnull=True, namespace_id__isnull=True)
            namespaced_filter = Q(namespace_type__isnull=False, namespace_id__isnull=False)
            if spec.has_wildcard:
                global_filter &= Q(namespace_wildcard=False)
                namespaced_filter &= Q(namespace_wildcard=False)

            wildcard_count = (
                spec.model.objects.filter(namespace_wildcard=True).count()
                if spec.has_wildcard
                else 0
            )
            scope_violations = spec.model.objects.filter(~namespace_scope_check()).count()
            wildcard_violations = (
                spec.model.objects.filter(namespace_wildcard=True)
                .exclude(namespace_type__isnull=True, namespace_id__isnull=True)
                .count()
                if spec.has_wildcard
                else 0
            )
            violations = scope_violations + wildcard_violations
            total_violations += violations

            parts = [
                f"table={spec.label}",
                f"global={spec.model.objects.filter(global_filter).count()}",
                f"namespaced={spec.model.objects.filter(namespaced_filter).count()}",
            ]
            if spec.has_wildcard:
                parts.append(f"wildcard={wildcard_count}")
            parts.append(f"violations={violations}")
            self.stdout.write(" ".join(parts))

        self.stdout.write(f"total_violations={total_violations}")
        if total_violations:
            raise CommandError("Namespace scope verification failed.")

    def ensure_namespace_schema(self, spec: NamespaceScopeSpec) -> None:
        table_name = spec.model._meta.db_table
        with connection.cursor() as cursor:
            columns = {
                column.name
                for column in connection.introspection.get_table_description(cursor, table_name)
            }
        missing = {"namespace_type", "namespace_id"} - columns
        if missing:
            missing_columns = ", ".join(sorted(missing))
            raise CommandError(
                f"{table_name} is missing namespace columns ({missing_columns}); "
                "run migrations first."
            )
