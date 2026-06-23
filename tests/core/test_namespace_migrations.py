from __future__ import annotations

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor


def constraint_names(table_name: str) -> set[str]:
    with connection.cursor() as cursor:
        return set(connection.introspection.get_constraints(cursor, table_name))


def assert_present(names: set[str], expected: set[str]) -> None:
    assert expected.issubset(names)


def assert_absent(names: set[str], unexpected: set[str]) -> None:
    assert names.isdisjoint(unexpected)


@pytest.mark.django_db(transaction=True)
def test_namespace_constraint_swap_migrations_are_reversible():
    executor = MigrationExecutor(connection)
    latest_targets = executor.loader.graph.leaf_nodes()
    before_targets = [
        ("assignments", "0001_initial"),
        ("service_auth", "0001_initial"),
        ("tags", "0003_tagalias"),
    ]
    after_targets = [
        ("assignments", "0002_remove_tagassignment_uniq_assignment_per_resource_tag_and_more"),
        ("service_auth", "0002_remove_serviceclientgrant_uniq_service_app_grant_and_more"),
        ("tags", "0004_remove_tag_uniq_active_shared_tag_slug_and_more"),
    ]

    old_tag_constraints = {
        "uniq_active_shared_tag_slug",
        "uniq_active_app_tag_slug",
        "uniq_active_shared_alias_slug",
        "uniq_active_app_alias_slug",
        "uniq_active_shared_vocab_slug",
        "uniq_active_app_vocab_slug",
    }
    new_tag_constraints = {
        "uniq_shared_global_tag_slug",
        "uniq_app_global_tag_slug",
        "uniq_app_ns_tag_slug",
        "uniq_shared_global_alias_slug",
        "uniq_app_global_alias_slug",
        "uniq_app_ns_alias_slug",
        "uniq_shared_global_vocab_slug",
        "uniq_app_global_vocab_slug",
        "uniq_app_ns_vocab_slug",
    }
    old_assignment_constraints = {"uniq_assignment_per_resource_tag"}
    new_assignment_constraints = {"uniq_global_assignment_tag", "uniq_ns_assignment_tag"}
    old_grant_constraints = {"uniq_service_app_grant", "uniq_service_tenant_grant"}
    new_grant_constraints = {
        "uniq_service_app_global_grant",
        "uniq_service_tenant_global",
        "uniq_service_app_ns_grant",
        "uniq_service_app_ns_wild",
        "uniq_service_tenant_ns_wild",
    }

    try:
        executor.migrate(before_targets)

        assert_present(
            constraint_names("tags"),
            {"uniq_active_shared_tag_slug", "uniq_active_app_tag_slug"},
        )
        assert_present(
            constraint_names("tag_aliases"),
            {"uniq_active_shared_alias_slug", "uniq_active_app_alias_slug"},
        )
        assert_present(
            constraint_names("vocabularies"),
            {"uniq_active_shared_vocab_slug", "uniq_active_app_vocab_slug"},
        )
        assert_absent(
            constraint_names("tags")
            | constraint_names("tag_aliases")
            | constraint_names("vocabularies"),
            new_tag_constraints,
        )
        assert_present(constraint_names("tag_assignments"), old_assignment_constraints)
        assert_absent(constraint_names("tag_assignments"), new_assignment_constraints)
        assert_present(constraint_names("service_client_grants"), old_grant_constraints)
        assert_absent(constraint_names("service_client_grants"), new_grant_constraints)

        executor = MigrationExecutor(connection)
        executor.migrate(after_targets)

        assert_absent(
            constraint_names("tags")
            | constraint_names("tag_aliases")
            | constraint_names("vocabularies"),
            old_tag_constraints,
        )
        assert_present(
            constraint_names("tags"),
            {"uniq_shared_global_tag_slug", "uniq_app_global_tag_slug", "uniq_app_ns_tag_slug"},
        )
        assert_present(
            constraint_names("tag_aliases"),
            {
                "uniq_shared_global_alias_slug",
                "uniq_app_global_alias_slug",
                "uniq_app_ns_alias_slug",
            },
        )
        assert_present(
            constraint_names("vocabularies"),
            {
                "uniq_shared_global_vocab_slug",
                "uniq_app_global_vocab_slug",
                "uniq_app_ns_vocab_slug",
            },
        )
        assert_absent(constraint_names("tag_assignments"), old_assignment_constraints)
        assert_present(constraint_names("tag_assignments"), new_assignment_constraints)
        assert_absent(constraint_names("service_client_grants"), old_grant_constraints)
        assert_present(constraint_names("service_client_grants"), new_grant_constraints)
    finally:
        executor = MigrationExecutor(connection)
        executor.migrate(latest_targets)
