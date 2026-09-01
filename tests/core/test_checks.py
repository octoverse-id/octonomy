from __future__ import annotations

import os
from pathlib import Path

import pytest
from django.core import checks as django_checks
from django.test import override_settings

from octonomy.core import checks
from octonomy.core.checks import (
    CONSTRAINT_SWAP_MIGRATIONS,
    admin_enabled_in_production_check,
    namespace_flag_dependencies,
    namespace_write_requires_swap,
    production_settings_check,
    static_root_populated_check,
    static_url_under_script_prefix_check,
)

POSTGRES_DATABASES = {"default": {"ENGINE": "django.db.backends.postgresql"}}
SQLITE_DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3"}}

# All flags on except the write kill-switch — the shipped steady state, and the
# baseline each single-flag violation below toggles away from.
VALID_DEFAULT_FLAGS = dict(
    NAMESPACE_SCHEMA_ENABLED=True,
    NAMESPACE_READ_ENABLED=True,
    NAMESPACE_AUTH_ENFORCED=True,
    NAMESPACE_V2_API_ENABLED=True,
    NAMESPACE_WRITE_ENABLED=False,
)


def _flag_check_ids(**overrides):
    flags = {**VALID_DEFAULT_FLAGS, **overrides}
    with override_settings(**flags):
        return {message.id for message in namespace_flag_dependencies(None)}


def test_namespace_flag_dependencies_accepts_default_combination():
    # Defaults (v2 read-only, writes off) are a valid, bootable combination.
    assert _flag_check_ids() == set()


def test_namespace_flag_dependencies_accepts_fully_enabled_combination():
    assert _flag_check_ids(NAMESPACE_WRITE_ENABLED=True) == set()


def test_namespace_flag_dependencies_accepts_all_off():
    assert (
        _flag_check_ids(
            NAMESPACE_SCHEMA_ENABLED=False,
            NAMESPACE_READ_ENABLED=False,
            NAMESPACE_AUTH_ENFORCED=False,
            NAMESPACE_V2_API_ENABLED=False,
            NAMESPACE_WRITE_ENABLED=False,
        )
        == set()
    )


def test_write_without_read_is_unbootable():
    # The headline invariant: v2 accepting namespaced writes that no read path can
    # return must refuse to boot.
    ids = _flag_check_ids(
        NAMESPACE_WRITE_ENABLED=True,
        NAMESPACE_READ_ENABLED=False,
        NAMESPACE_AUTH_ENFORCED=False,
        NAMESPACE_V2_API_ENABLED=False,
    )
    assert ids == {"octonomy.E013"}


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        # READ requires SCHEMA.
        (
            dict(
                NAMESPACE_SCHEMA_ENABLED=False,
                NAMESPACE_AUTH_ENFORCED=False,
                NAMESPACE_V2_API_ENABLED=False,
            ),
            {"octonomy.E010"},
        ),
        # AUTH requires READ.
        (
            dict(NAMESPACE_READ_ENABLED=False, NAMESPACE_V2_API_ENABLED=False),
            {"octonomy.E011"},
        ),
        # WRITE requires SCHEMA (co-occurs with E010: READ is on but SCHEMA is off).
        (
            dict(
                NAMESPACE_SCHEMA_ENABLED=False,
                NAMESPACE_WRITE_ENABLED=True,
                NAMESPACE_AUTH_ENFORCED=False,
                NAMESPACE_V2_API_ENABLED=False,
            ),
            {"octonomy.E010", "octonomy.E012"},
        ),
        # V2_API requires READ and AUTH (both fire: READ off drops AUTH's basis too).
        (
            dict(
                NAMESPACE_READ_ENABLED=False,
                NAMESPACE_AUTH_ENFORCED=False,
                NAMESPACE_V2_API_ENABLED=True,
            ),
            {"octonomy.E014", "octonomy.E015"},
        ),
        # V2_API requires AUTH, isolated (READ on so only the AUTH rule fires).
        (
            dict(NAMESPACE_AUTH_ENFORCED=False, NAMESPACE_V2_API_ENABLED=True),
            {"octonomy.E015"},
        ),
    ],
)
def test_namespace_flag_dependencies_rejects_invalid_combinations(overrides, expected):
    assert _flag_check_ids(**overrides) == expected


def test_write_swap_check_skips_when_writes_disabled(monkeypatch):
    called = False

    def _fail():  # pragma: no cover - must not be called
        nonlocal called
        called = True
        return set()

    monkeypatch.setattr(checks, "_applied_migrations", _fail)
    with override_settings(NAMESPACE_WRITE_ENABLED=False):
        assert namespace_write_requires_swap(None) == []
    assert called is False


def test_write_swap_check_passes_when_swap_applied(monkeypatch):
    monkeypatch.setattr(checks, "_applied_migrations", lambda: set(CONSTRAINT_SWAP_MIGRATIONS))
    with override_settings(NAMESPACE_WRITE_ENABLED=True):
        assert namespace_write_requires_swap(None) == []


def test_write_swap_check_errors_when_swap_missing(monkeypatch):
    monkeypatch.setattr(checks, "_applied_migrations", lambda: set())
    with override_settings(NAMESPACE_WRITE_ENABLED=True):
        ids = {message.id for message in namespace_write_requires_swap(None)}
    assert ids == {"octonomy.E016"}


def test_write_swap_check_fails_closed_when_undeterminable(monkeypatch):
    # No migrations table / unreachable DB while writes are enabled => cannot verify
    # the swap, so fail closed with E016 rather than passing on faith.
    monkeypatch.setattr(checks, "_applied_migrations", lambda: None)
    with override_settings(NAMESPACE_WRITE_ENABLED=True):
        ids = {message.id for message in namespace_write_requires_swap(None)}
    assert ids == {"octonomy.E016"}


def test_write_swap_check_skips_undeterminable_when_writes_disabled(monkeypatch):
    # Writes off: an undeterminable migration state is irrelevant, so no error.
    monkeypatch.setattr(checks, "_applied_migrations", lambda: None)
    with override_settings(NAMESPACE_WRITE_ENABLED=False):
        assert namespace_write_requires_swap(None) == []


@pytest.mark.django_db
def test_write_swap_check_reads_real_migration_state():
    # Exercises the real _applied_migrations() seam against the migrated test DB,
    # where the S1 constraint-swap migrations are applied, so the gate passes.
    assert set(CONSTRAINT_SWAP_MIGRATIONS) <= (checks._applied_migrations() or set())
    with override_settings(NAMESPACE_WRITE_ENABLED=True):
        assert namespace_write_requires_swap(None) == []


@override_settings(DEBUG=True)
def test_production_settings_check_skips_debug():
    assert production_settings_check(None) == []


@override_settings(
    DEBUG=False,
    SECRET_KEY="release-secret",
    SERVICE_TOKEN_PEPPER="release-pepper",
    ALLOWED_HOSTS=["api.example.com"],
    DATABASES=POSTGRES_DATABASES,
)
def test_production_settings_check_accepts_safe_production_settings():
    assert production_settings_check(None) == []


@override_settings(
    DEBUG=False,
    SECRET_KEY="local-dev-secret",
    SERVICE_TOKEN_PEPPER="local-dev-service-token-pepper",
    ALLOWED_HOSTS=["*"],
    DATABASES=SQLITE_DATABASES,
)
def test_production_settings_check_reports_unsafe_production_settings():
    ids = {message.id for message in production_settings_check(None)}

    assert ids == {"octonomy.E001", "octonomy.E002", "octonomy.E004", "octonomy.E005"}


@override_settings(
    DEBUG=False,
    SECRET_KEY="release-secret",
    SERVICE_TOKEN_PEPPER="release-pepper",
    ALLOWED_HOSTS=[],
    DATABASES=POSTGRES_DATABASES,
)
def test_production_settings_check_requires_allowed_hosts():
    ids = {message.id for message in production_settings_check(None)}

    assert ids == {"octonomy.E003"}


@override_settings(DEBUG=False, ADMIN_ENABLED=True)
def test_admin_in_production_emits_w001_warning():
    messages = admin_enabled_in_production_check(None)
    ids = {message.id for message in messages}
    assert ids == {"octonomy.W001"}
    # A Warning, not an Error: it surfaces on `check --deploy` but must not block.
    assert all(message.is_serious() is False for message in messages)


@override_settings(DEBUG=False, ADMIN_ENABLED=False)
def test_admin_disabled_in_production_is_silent():
    assert admin_enabled_in_production_check(None) == []


@override_settings(DEBUG=True, ADMIN_ENABLED=True)
def test_admin_enabled_in_debug_does_not_warn():
    # Enabled in local development is the normal case; W001 is production-only.
    assert admin_enabled_in_production_check(None) == []


# --- octonomy.W002: STATIC_ROOT has nothing to serve (#143) --------------------------


# The suite runs the plain staticfiles backend, which has no manifest, so W002's manifest
# leg is dormant by default. Tests that need it opt in with this.
MANIFEST_STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}


@pytest.fixture
def populated_static_root(tmp_path):
    """A STATIC_ROOT that looks like collectstatic ran."""

    root = tmp_path / "staticfiles"
    (root / "admin" / "css").mkdir(parents=True)
    (root / "admin" / "css" / "base.css").write_text("body{}")
    return str(root)


@pytest.fixture
def empty_static_root(tmp_path):
    """A STATIC_ROOT that exists but was never collected into."""

    root = tmp_path / "staticfiles"
    root.mkdir()
    return str(root)


def test_w002_fires_on_an_empty_static_root(empty_static_root):
    # T-D1. The VPS install case: nginx aliases a directory nobody ever filled.
    with override_settings(DEBUG=False, ADMIN_ENABLED=True, STATIC_ROOT=empty_static_root):
        messages = static_root_populated_check(None)

    assert {message.id for message in messages} == {"octonomy.W002"}


def test_w002_fires_on_a_missing_static_root(tmp_path):
    with override_settings(
        DEBUG=False, ADMIN_ENABLED=True, STATIC_ROOT=str(tmp_path / "never-created")
    ):
        assert {m.id for m in static_root_populated_check(None)} == {"octonomy.W002"}


def test_w002_is_silent_once_static_is_collected(populated_static_root):
    # T-D2.
    with override_settings(DEBUG=False, ADMIN_ENABLED=True, STATIC_ROOT=populated_static_root):
        assert static_root_populated_check(None) == []


def test_w002_is_silent_when_the_admin_is_disabled(empty_static_root):
    # T-D3. Accepted ceiling of dec-797303d8: the browsable API still needs static here,
    # and this check deliberately says nothing about it.
    with override_settings(DEBUG=False, ADMIN_ENABLED=False, STATIC_ROOT=empty_static_root):
        assert static_root_populated_check(None) == []


def test_w002_is_silent_in_debug(empty_static_root):
    # T-D4. DEBUG serves through the finders, so an empty STATIC_ROOT is normal locally.
    with override_settings(DEBUG=True, ADMIN_ENABLED=True, STATIC_ROOT=empty_static_root):
        assert static_root_populated_check(None) == []


def test_w002_is_a_warning_not_an_error(empty_static_root):
    # T-D6. A missing optional console must never take down a healthy REST API.
    with override_settings(DEBUG=False, ADMIN_ENABLED=True, STATIC_ROOT=empty_static_root):
        messages = static_root_populated_check(None)

    assert all(message.is_serious() is False for message in messages)


def test_w002_runs_on_a_plain_check_not_only_on_deploy(empty_static_root):
    """T-D5 — the one that stops this silently regressing to deploy-only.

    ``django/core/management/commands/check.py`` only runs deployment checks when
    ``--deploy`` is passed, and neither ``docker-entrypoint.sh`` nor the systemd
    ``ExecStartPre`` passes it. A deploy-tagged W002 would therefore never fire on the
    channels it exists for. This runs the registry exactly the way a bare
    ``manage.py check`` does.
    """

    from django.core.checks.registry import registry

    assert static_root_populated_check in registry.registered_checks
    assert static_root_populated_check not in registry.deployment_checks

    with override_settings(DEBUG=False, ADMIN_ENABLED=True, STATIC_ROOT=empty_static_root):
        ids = {m.id for m in django_checks.run_checks(include_deployment_checks=False)}

    assert "octonomy.W002" in ids


def test_w002_fires_when_the_root_holds_only_empty_directories(tmp_path):
    # An interrupted collectstatic leaves the tree without the files. A predicate that
    # only asked "does STATIC_ROOT contain any entry?" would call this collected and stay
    # silent while every admin render still failed.
    root = tmp_path / "staticfiles"
    (root / "admin" / "css").mkdir(parents=True)

    with override_settings(DEBUG=False, ADMIN_ENABLED=True, STATIC_ROOT=str(root)):
        assert {m.id for m in static_root_populated_check(None)} == {"octonomy.W002"}


def test_w002_fires_when_collected_files_are_unreadable(tmp_path):
    # The VPS failure mode: collectstatic run as root under a restrictive umask leaves a
    # fully populated tree the service account cannot read. Indistinguishable from an
    # uncollected root at request time, so it must warn the same way.
    root = tmp_path / "staticfiles"
    (root / "admin").mkdir(parents=True)
    asset = root / "admin" / "base.css"
    asset.write_text("body{}")
    asset.chmod(0o000)

    if os.access(asset, os.R_OK):  # pragma: no cover - running as root
        pytest.skip("this process can read mode-000 files (running as root)")

    with override_settings(DEBUG=False, ADMIN_ENABLED=True, STATIC_ROOT=str(root)):
        ids = {m.id for m in static_root_populated_check(None)}

    asset.chmod(0o644)  # let tmp_path cleanup work
    assert ids == {"octonomy.W002"}


def test_w002_fires_when_the_manifest_backend_has_no_manifest(populated_static_root):
    # Populated but collected without (or before) the manifest backend: {% static %} then
    # resolves through a staticfiles.json that is not there, so the page 500s at render.
    # The file check alone passes here — this is the leg that catches it.
    with override_settings(
        DEBUG=False,
        ADMIN_ENABLED=True,
        STATIC_ROOT=populated_static_root,
        STORAGES=MANIFEST_STORAGES,
    ):
        assert {m.id for m in static_root_populated_check(None)} == {"octonomy.W002"}


def test_w002_is_silent_when_the_manifest_backend_has_its_manifest(populated_static_root):
    (Path(populated_static_root) / "staticfiles.json").write_text('{"paths": {}, "version": "1.1"}')

    with override_settings(
        DEBUG=False,
        ADMIN_ENABLED=True,
        STATIC_ROOT=populated_static_root,
        STORAGES=MANIFEST_STORAGES,
    ):
        assert static_root_populated_check(None) == []


def test_w002_fires_when_static_root_is_unset():
    with override_settings(DEBUG=False, ADMIN_ENABLED=True, STATIC_ROOT=None):
        assert {m.id for m in static_root_populated_check(None)} == {"octonomy.W002"}


def test_w002_reports_a_corrupt_manifest_instead_of_crashing_the_check(populated_static_root):
    """A broken asset manifest must not be able to abort the boot.

    Touching ``staticfiles_storage`` instantiates the backend, and
    ``ManifestFilesMixin.__init__`` raises ``ValueError`` on a manifest it cannot parse.
    Letting that escape a system check makes ``manage.py check`` exit non-zero, which
    aborts ``docker-entrypoint.sh`` — so the optional admin console would take a healthy
    REST API down with it. W002 must degrade to a warning here.
    """

    (Path(populated_static_root) / "staticfiles.json").write_text('{"version": "9.9", "paths": {}}')

    with override_settings(
        DEBUG=False,
        ADMIN_ENABLED=True,
        STATIC_ROOT=populated_static_root,
        STORAGES=MANIFEST_STORAGES,
    ):
        messages = static_root_populated_check(None)

    assert {m.id for m in messages} == {"octonomy.W002"}
    assert all(m.is_serious() is False for m in messages)


def test_w002_does_not_swallow_a_manifest_failure_the_serving_path_cannot_survive(
    populated_static_root,
):
    """The downgrade is ValueError-only, on purpose.

    An unreadable staticfiles.json raises PermissionError, and WhiteNoise's startup probe
    catches only ValueError — so the WSGI application cannot be constructed either.
    Reporting that as a mere Warning would let ``manage.py check`` pass green and then
    crash-loop Gunicorn with no explanation. Letting it escape fails the check with the
    real error instead, which is the honest outcome.
    """

    manifest = Path(populated_static_root) / "staticfiles.json"
    manifest.write_text('{"version": "1.1", "paths": {}}')
    manifest.chmod(0o000)

    if os.access(manifest, os.R_OK):  # pragma: no cover - running as root
        manifest.chmod(0o644)
        pytest.skip("this process can read mode-000 files (running as root)")

    try:
        with override_settings(
            DEBUG=False,
            ADMIN_ENABLED=True,
            STATIC_ROOT=populated_static_root,
            STORAGES=MANIFEST_STORAGES,
        ):
            with pytest.raises(PermissionError):
                static_root_populated_check(None)
    finally:
        manifest.chmod(0o644)  # let tmp_path cleanup work


# --- octonomy.W003: STATIC_URL and FORCE_SCRIPT_NAME are a pair (#143) ----------------


@pytest.mark.parametrize(
    ("static_url", "script_name"),
    [
        # The pair, set consistently — the documented subpath recipe.
        ("/octonomy/static/", "/octonomy"),
        # A trailing slash on the script name must not change the verdict.
        ("/octonomy/static/", "/octonomy/"),
        # Assets on a CDN: nothing local left to reconcile, and pairing a CDN with a
        # subpath app is a real topology.
        ("https://cdn.example.com/static/", "/octonomy"),
        ("//cdn.example.com/static/", "/octonomy"),
        # Root mount: no script prefix, so nothing to be inconsistent with. Renaming the
        # static path at a root mount is ordinary and must never be flagged.
        ("/static/", None),
        ("/assets/", None),
    ],
)
def test_w003_is_silent_on_coherent_configurations(static_url, script_name):
    with override_settings(STATIC_URL=static_url, FORCE_SCRIPT_NAME=script_name):
        assert static_url_under_script_prefix_check(None) == []


@pytest.mark.parametrize(
    ("static_url", "script_name"),
    [
        # The half-configured case: FORCE_SCRIPT_NAME set, STATIC_URL left at its default.
        # Templates link /static/... while the app lives at /octonomy/.
        ("/static/", "/octonomy"),
        # Two different local prefixes.
        ("/other/static/", "/octonomy"),
        # A near miss that is not actually a path segment match.
        ("/octonomyx/static/", "/octonomy"),
    ],
)
def test_w003_flags_static_links_outside_the_apps_mount(static_url, script_name):
    with override_settings(STATIC_URL=static_url, FORCE_SCRIPT_NAME=script_name):
        messages = static_url_under_script_prefix_check(None)

    assert {m.id for m in messages} == {"octonomy.W003"}
    # A Warning, never an Error: whether this actually breaks depends on proxy routing the
    # process cannot see, so it must not be able to refuse a working deployment.
    assert all(m.is_serious() is False for m in messages)


def test_w003_runs_on_a_plain_check_not_only_on_deploy():
    from django.core.checks.registry import registry

    assert static_url_under_script_prefix_check in registry.registered_checks
    assert static_url_under_script_prefix_check not in registry.deployment_checks

    with override_settings(STATIC_URL="/static/", FORCE_SCRIPT_NAME="/octonomy"):
        ids = {m.id for m in django_checks.run_checks(include_deployment_checks=False)}

    assert "octonomy.W003" in ids
