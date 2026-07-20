from __future__ import annotations

import pytest
from django.test import override_settings

from octonomy.core import checks
from octonomy.core.checks import (
    CONSTRAINT_SWAP_MIGRATIONS,
    namespace_flag_dependencies,
    namespace_write_requires_swap,
    production_settings_check,
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
