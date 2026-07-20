from __future__ import annotations

from django.conf import settings
from django.core.checks import Error, Tags, register

DEFAULT_SECRET_KEY = "local-dev-secret"
DEFAULT_SERVICE_TOKEN_PEPPER = "local-dev-service-token-pepper"

# Default posture for the namespace rollout flags (S7, issue #45). SCHEMA/READ/
# AUTH/V2_API default on (the shipped S2-S6 behaviour); the WRITE kill-switch
# defaults off and flips last. getattr falls back to these so an absent setting
# never spuriously trips the dependency check.
_NAMESPACE_FLAG_DEFAULTS = {
    "NAMESPACE_SCHEMA_ENABLED": True,
    "NAMESPACE_READ_ENABLED": True,
    "NAMESPACE_AUTH_ENFORCED": True,
    "NAMESPACE_V2_API_ENABLED": True,
    "NAMESPACE_WRITE_ENABLED": False,
}

# Rollout dependency ladder: each (enabled -> required) implication, its stable
# error id, and the operator-facing reason. Enabling a flag without its
# prerequisite is unbootable. The critical rule is E013 (WRITE requires READ):
# it forbids persisting namespaced rows that no read path can return — the
# "v2 accepting writes nobody can read" combination the epic calls out.
_NAMESPACE_FLAG_RULES = (
    (
        "NAMESPACE_READ_ENABLED",
        "NAMESPACE_SCHEMA_ENABLED",
        "octonomy.E010",
        "NAMESPACE_READ_ENABLED requires NAMESPACE_SCHEMA_ENABLED: namespace-aware "
        "reads need the namespace columns from the S1 schema.",
    ),
    (
        "NAMESPACE_AUTH_ENFORCED",
        "NAMESPACE_READ_ENABLED",
        "octonomy.E011",
        "NAMESPACE_AUTH_ENFORCED requires NAMESPACE_READ_ENABLED: enforcing namespace "
        "authorization without namespace-aware reads would deny at auth while reads "
        "stay global.",
    ),
    (
        "NAMESPACE_WRITE_ENABLED",
        "NAMESPACE_SCHEMA_ENABLED",
        "octonomy.E012",
        "NAMESPACE_WRITE_ENABLED requires NAMESPACE_SCHEMA_ENABLED: namespaced rows "
        "cannot be persisted without the S1 namespace columns and constraints.",
    ),
    (
        "NAMESPACE_WRITE_ENABLED",
        "NAMESPACE_READ_ENABLED",
        "octonomy.E013",
        "NAMESPACE_WRITE_ENABLED requires NAMESPACE_READ_ENABLED: persisting namespaced "
        "rows that no read path can return would strand merchant data (v2 accepting "
        "writes nobody can read). Enable reads before writes; disable writes before "
        "reads on rollback.",
    ),
    (
        "NAMESPACE_V2_API_ENABLED",
        "NAMESPACE_READ_ENABLED",
        "octonomy.E014",
        "NAMESPACE_V2_API_ENABLED requires NAMESPACE_READ_ENABLED: the v2 surface must "
        "not accept namespaced traffic without namespace-aware reads.",
    ),
    (
        "NAMESPACE_V2_API_ENABLED",
        "NAMESPACE_AUTH_ENFORCED",
        "octonomy.E015",
        "NAMESPACE_V2_API_ENABLED requires NAMESPACE_AUTH_ENFORCED: exposing v2 without "
        "namespace authorization enforcement risks cross-namespace reads.",
    ),
)

# The S1 constraint-swap migrations. Merchant writes may only be enabled once the
# namespace-aware unique constraints have replaced the old global-only ones;
# otherwise the headline "two merchants, same slug" case fails with duplicate-key
# errors. Checked deploy-only (never during `manage.py migrate`).
CONSTRAINT_SWAP_MIGRATIONS = (
    ("tags", "0004_remove_tag_uniq_active_shared_tag_slug_and_more"),
    ("assignments", "0002_remove_tagassignment_uniq_assignment_per_resource_tag_and_more"),
)


@register(Tags.security, deploy=True)
def production_settings_check(app_configs, **kwargs):
    if settings.DEBUG:
        return []

    messages = []
    messages.extend(_check_secret_key())
    messages.extend(_check_service_token_pepper())
    messages.extend(_check_allowed_hosts())
    messages.extend(_check_database_engine())
    return messages


def _check_secret_key():
    if settings.SECRET_KEY and settings.SECRET_KEY != DEFAULT_SECRET_KEY:
        return []
    return [
        Error(
            "DJANGO_SECRET_KEY must be set to a non-default value when DJANGO_DEBUG=false.",
            id="octonomy.E001",
        )
    ]


def _check_service_token_pepper():
    pepper = getattr(settings, "SERVICE_TOKEN_PEPPER", "")
    if pepper and pepper != DEFAULT_SERVICE_TOKEN_PEPPER:
        return []
    return [
        Error(
            "SERVICE_TOKEN_PEPPER must be set to a non-default value when DJANGO_DEBUG=false.",
            id="octonomy.E002",
        )
    ]


def _check_allowed_hosts():
    allowed_hosts = [host for host in getattr(settings, "ALLOWED_HOSTS", []) if host]
    if not allowed_hosts:
        return [
            Error(
                "ALLOWED_HOSTS must include at least one production host when DJANGO_DEBUG=false.",
                id="octonomy.E003",
            )
        ]
    if "*" not in allowed_hosts:
        return []
    return [
        Error(
            "ALLOWED_HOSTS must not use '*' for production deployments.",
            id="octonomy.E004",
        )
    ]


def _check_database_engine():
    engine = settings.DATABASES.get("default", {}).get("ENGINE", "")
    if "sqlite" not in engine:
        return []
    return [
        Error(
            "Production deployments must use PostgreSQL instead of SQLite.",
            id="octonomy.E005",
        )
    ]


def _namespace_flag(name: str) -> bool:
    return bool(getattr(settings, name, _NAMESPACE_FLAG_DEFAULTS[name]))


@register(Tags.compatibility)
def namespace_flag_dependencies(app_configs, **kwargs):
    """Refuse to boot on an invalid namespace rollout flag combination.

    Runs on every ``manage.py check`` (not deploy-only) because it reads settings
    alone and never touches the database, so it is safe before migrations apply.
    """

    messages = []
    for enabled, required, ident, reason in _NAMESPACE_FLAG_RULES:
        if _namespace_flag(enabled) and not _namespace_flag(required):
            messages.append(
                Error(
                    reason,
                    id=ident,
                    hint=f"Set {required}=true, or disable {enabled}.",
                )
            )
    return messages


def _applied_migrations():
    """Applied ``(app_label, name)`` migrations, or ``None`` if undeterminable.

    Isolated as a seam so the deploy check can be unit-tested without a migrated
    database. Returns ``None`` when migration state cannot be determined (the
    migrations table is absent, or the database is unreachable); the caller treats
    that as a verification failure and fails closed rather than crashing the check.
    """

    from django.db import Error as DatabaseError
    from django.db import connection
    from django.db.migrations.recorder import MigrationRecorder

    recorder = MigrationRecorder(connection)
    try:
        if not recorder.has_table():
            return None
        return set(recorder.applied_migrations())
    except DatabaseError:
        return None


@register(Tags.database, deploy=True)
def namespace_write_requires_swap(app_configs, **kwargs):
    """Gate merchant writes on the S1 constraint swap being applied.

    Deploy-tagged so it runs under ``manage.py check --deploy`` but never during
    ``manage.py migrate`` — checks run before migrations apply, so gating writes on
    an applied migration unconditionally at boot would deadlock the very migration
    that satisfies it (the epic's explicit footgun).
    """

    if not _namespace_flag("NAMESPACE_WRITE_ENABLED"):
        return []

    applied = _applied_migrations()
    if applied is None:
        # Fail closed: with writes enabled we could not confirm the swap is applied
        # (unreachable or unmigrated database). Passing here would let a deploy clear
        # `check --deploy` and then accept namespaced writes against a database still
        # on the old global-only constraints — the exact case this gate prevents.
        return [
            Error(
                "NAMESPACE_WRITE_ENABLED is set but the applied-migration state could not be "
                "verified (database unreachable or not yet migrated); refusing to confirm the "
                "S1 constraint swap is in place.",
                id="octonomy.E016",
                hint="Ensure the database is reachable and migrated before enabling namespaced "
                "writes.",
            )
        ]

    missing = [m for m in CONSTRAINT_SWAP_MIGRATIONS if m not in applied]
    if not missing:
        return []

    formatted = ", ".join(f"{app}.{name}" for app, name in missing)
    return [
        Error(
            "NAMESPACE_WRITE_ENABLED requires the S1 constraint-swap migrations to be "
            f"applied first; missing: {formatted}.",
            id="octonomy.E016",
            hint="Run `python manage.py migrate` before enabling namespaced writes.",
        )
    ]
