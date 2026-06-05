from __future__ import annotations

from django.test import override_settings

from octonomy.core.checks import production_settings_check

POSTGRES_DATABASES = {"default": {"ENGINE": "django.db.backends.postgresql"}}
SQLITE_DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3"}}


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
