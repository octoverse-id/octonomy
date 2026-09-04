from __future__ import annotations

import os
from pathlib import Path

from django.conf import settings
from django.contrib.staticfiles.storage import staticfiles_storage
from django.core.checks import Error, Tags, Warning, register

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


@register(Tags.security, deploy=True)
def admin_enabled_in_production_check(app_configs, **kwargs):
    """Warn — but do not block — when the operator admin is enabled with DEBUG=false.

    The Unfold admin is a trusted development/operator interface, not a public surface.
    Enabling it in production is a deliberate, supported operator choice (protected by
    HTTPS, non-default secrets, and superuser-only access), so this is a Warning, not
    an Error: ``manage.py check --deploy`` surfaces it without failing. It is
    deploy-tagged and gated on ``not DEBUG`` so it never fires in local development.
    """

    if settings.DEBUG or not getattr(settings, "ADMIN_ENABLED", False):
        return []
    return [
        Warning(
            "The Octonomy admin is enabled with DEBUG=false. It is intended as a "
            "trusted development/operator interface, not a public API surface; the REST "
            "API remains the primary surface.",
            hint="Serve it over HTTPS, restrict access to trusted operators, and unset "
            "OCTONOMY_ADMIN_ENABLED to disable it. See docs/operations.md 'Admin console'.",
            id="octonomy.W001",
        )
    ]


def _contains_a_readable_file(root: Path) -> bool:
    """True when at least one regular file under ``root`` is readable by this process.

    Deliberately NOT ``any(root.iterdir())``: an interrupted ``collectstatic`` can leave
    ``STATIC_ROOT/admin/`` behind with nothing in it, and a bare directory entry would
    make an unusable root look collected. ``rglob`` yields nothing for a missing path or a
    path that is a file, so those cases fall through to False without a guard.

    ``os.access`` matters on the VPS channel specifically: ``collectstatic`` run as root
    under a restrictive umask produces a fully populated tree that the service account
    cannot read, which fails exactly like an uncollected one. Short-circuits on the first
    hit, so the walk costs a handful of ``scandir`` calls in the healthy case.
    """

    try:
        return any(entry.is_file() and os.access(entry, os.R_OK) for entry in root.rglob("*"))
    except OSError:
        return False


def _static_readiness_problem() -> str | None:
    """Describe why STATIC_ROOT cannot back the rendered pages, or None when collected."""

    static_root = getattr(settings, "STATIC_ROOT", None)
    if not static_root:
        return "STATIC_ROOT is not set"

    root = Path(static_root)
    if not _contains_a_readable_file(root):
        return f"STATIC_ROOT ({root}) is missing, empty, or holds no readable file"

    # Only a manifest backend has a manifest_name, so this asks the configured storage
    # what it needs rather than matching on a backend string. A populated tree collected
    # WITHOUT the manifest (e.g. an upgrade that changed backends, or a tree copied from
    # an older release) passes the file check above and still 500s on the first render,
    # because HashedFilesMixin resolves {% static %} through staticfiles.json.
    #
    # The try is load-bearing, not defensive habit. Touching staticfiles_storage is what
    # instantiates the backend, and ManifestFilesMixin.__init__ reads staticfiles.json
    # eagerly and raises ValueError on a corrupt or wrong-version file. Raising out of a
    # system check makes `manage.py check` exit non-zero, which aborts
    # docker-entrypoint.sh — so a broken asset manifest would take down a perfectly
    # healthy REST API along with the optional console. Verified: without this guard, a
    # staticfiles.json carrying an unknown version fails the boot. Report it as the
    # warning instead; that is the whole point of W002 being a Warning.
    #
    # ValueError ONLY, deliberately narrow: downgrade exactly the failures the serving
    # path also survives. WhiteNoiseMiddleware's startup probe calls the same storage and
    # catches ValueError alone (whitenoise/middleware.py get_static_url), so a corrupt
    # manifest really does leave a bootable app. Anything else — an unreadable manifest
    # raising PermissionError is the case to think about — kills middleware construction
    # too, and swallowing it here would pass `manage.py check` green and then crash-loop
    # Gunicorn. Better to let that escape and fail the check with the real error.
    try:
        manifest_name = getattr(staticfiles_storage, "manifest_name", None)
    except ValueError as exc:
        return (
            f"the configured staticfiles backend could not load its manifest from "
            f"STATIC_ROOT ({root}): {exc}"
        )

    if manifest_name and not (root / manifest_name).is_file():
        return (
            f"STATIC_ROOT ({root}) has no {manifest_name}, which the configured manifest "
            "staticfiles backend needs to resolve asset URLs"
        )

    return None


@register(Tags.staticfiles)
def static_root_populated_check(app_configs, **kwargs):
    """Warn when DEBUG=false but STATIC_ROOT cannot back the pages this app renders.

    Octonomy serves its own bundled assets through WhiteNoise, which indexes STATIC_ROOT
    at process start. A deploy that never ran ``collectstatic`` therefore has an empty
    index, and — under the manifest staticfiles backend — the first HTML render raises
    instead of merely looking unstyled. Name that at boot rather than at the first
    operator request.

    NOT gated on ``ADMIN_ENABLED``, and that is the correction #146 forced. The gate was
    an accepted ceiling (dec-797303d8) while the admin was the only page anyone was
    likely to open: the DRF browsable API needed static too, but it is a developer
    convenience. Self-hosting the docs assets added ``/api/docs/swagger/`` and the three
    Redoc pages to the list — the product's PRIMARY documented surface, always on, and
    they now 500 under manifest storage on an uncollected root where they previously
    rendered from a CDN. A warning that stays silent for the default deployment shape
    (admin off) would have been silent for exactly the deployments that regressed.

    This is a readiness heuristic, not a proof: it catches an absent, empty, unreadable or
    manifest-less STATIC_ROOT. It deliberately says nothing about a populated-but-STALE
    root (an upgrade that skipped ``collectstatic`` leaves assets that look fine here);
    that one is addressed by the runbook corrections in #145.

    Registered WITHOUT ``deploy=True`` on purpose, following the
    ``namespace_flag_dependencies`` precedent rather than W001's. Deployment checks only
    run when ``--deploy`` is passed (``django/core/management/commands/check.py``), and
    neither ``docker-entrypoint.sh`` nor ``deploy/systemd/octonomy.service`` passes it —
    a deploy-tagged version of this check would never fire on the channels that need it.
    It reads settings and the filesystem only, never the database, so it is safe to run
    before migrations apply.

    A ``Warning``, never an ``Error``: unrenderable HTML pages must not take down a
    healthy JSON API, which needs no static at all.
    """

    if settings.DEBUG:
        return []

    problem = _static_readiness_problem()
    if problem is None:
        return []

    return [
        Warning(
            f"DEBUG is false but {problem}, so the app has no bundled CSS/JS to serve. "
            "Every HTML surface fails to render: the /api/docs/ Swagger and Redoc pages "
            "and the DRF browsable API, plus the admin console when it is enabled. The "
            "JSON API is unaffected.",
            hint="Run `python manage.py collectstatic --noinput` on this host and restart "
            "the service — the asset index is built at process start, so collecting "
            "afterwards is not picked up promptly (Gunicorn workers only re-index as they "
            "recycle). Container images bake the assets in at build time, so an empty "
            "STATIC_ROOT there points at a broken image rather than a missing step; do "
            "not run collectstatic inside a read-only container filesystem. Note this "
            "check runs from `manage.py check`, which the container entrypoint and the "
            "systemd ExecStartPre invoke — a bare `gunicorn config.wsgi:application` that "
            "bypasses them is not covered.",
            id="octonomy.W002",
        )
    ]


@register(Tags.staticfiles)
def static_url_under_script_prefix_check(app_configs, **kwargs):
    """Warn when a subpath deployment points asset links outside its own mount.

    ``OCTONOMY_STATIC_URL`` and ``OCTONOMY_FORCE_SCRIPT_NAME`` are a pair: the first
    decides the URL browsers are told to fetch, the second decides the prefix everything
    else is generated under. Set the second alone and templates emit ``/static/...`` while
    the app lives at ``/octonomy/`` — a proxy routing only ``/octonomy/*`` never sees the
    request, and the assets 404 from the browser's side even though the app would have
    served them happily.

    A ``Warning``, not an ``Error``, and the distinction is not politeness. Whether that
    combination actually breaks depends on proxy routing this process cannot see: an
    operator who deliberately routes ``/static/`` at the host root to this app has a
    working deployment, and refusing to boot would reject it. Name the suspicious shape;
    do not overrule the operator.

    Only the decidable direction is checked. The mirror case — a prefixed STATIC_URL with
    no FORCE_SCRIPT_NAME — is indistinguishable from simply renaming the static path
    (``STATIC_URL=/assets/``), which is perfectly ordinary at a root mount, so there is no
    honest rule for it.
    """

    script_name = getattr(settings, "FORCE_SCRIPT_NAME", None)
    if not script_name:
        return []

    static_url = getattr(settings, "STATIC_URL", "") or ""
    # An absolute URL puts the assets on another host entirely (a CDN). There is no local
    # prefix left to reconcile, and pairing a CDN with a subpath app is a real topology.
    if static_url.startswith(("http://", "https://", "//")):
        return []

    prefix = f"/{script_name.strip('/')}/"
    if static_url.startswith(prefix):
        return []

    return [
        Warning(
            f"FORCE_SCRIPT_NAME is {script_name!r}, so this app is mounted at {prefix} — "
            f"but STATIC_URL is {static_url!r}, which is not under it. Templates will link "
            "assets outside the app's own mount point.",
            hint="Set OCTONOMY_STATIC_URL to a path under OCTONOMY_FORCE_SCRIPT_NAME (e.g. "
            f"OCTONOMY_STATIC_URL={prefix}static/), or point it at a full http(s) CDN URL. "
            "Ignore this if your proxy deliberately routes STATIC_URL to this app at the "
            "host root — that works, it is just not something this process can confirm.",
            id="octonomy.W003",
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
