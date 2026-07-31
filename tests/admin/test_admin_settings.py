"""Env-driven settings derivation for the admin foundation (#84, plan GAP#1/#2).

These settings (ADMIN_ENABLED, SESSION_COOKIE_SECURE, CSRF_COOKIE_SECURE) are computed
once at settings-import time from the environment, so ``override_settings`` cannot
exercise the derivation. Each case re-imports ``config.settings`` in a fresh process
with a controlled environment and reads the resulting value back as JSON.

``config.settings`` auto-loads the repo ``.env`` via ``load_dotenv``, which would leak
developer-local values (e.g. ``OCTONOMY_ADMIN_ENABLED``) into the "unset default"
cases. The subprocess neutralizes ``dotenv.load_dotenv`` before importing settings so
the constructed environment is the *only* input — making these tests hermetic
regardless of the local ``.env``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

# A valid DEBUG=false production baseline: settings.py refuses to import with the
# default secret/pepper once DEBUG is off, so always supply non-default values.
_PROD_BASE = {
    "DJANGO_SETTINGS_MODULE": "config.settings",
    "DJANGO_SECRET_KEY": "release-secret-xyz",
    "SERVICE_TOKEN_PEPPER": "release-pepper-xyz",
    "ALLOWED_HOSTS": "api.example.com",
}

# Env keys these tests own; cleared before applying per-case values so the ambient
# environment (and the repo .env) cannot leak in.
_OWNED_KEYS = (
    "DJANGO_DEBUG",
    "OCTONOMY_ADMIN_ENABLED",
    "SESSION_COOKIE_SECURE",
    "CSRF_COOKIE_SECURE",
    "OCTONOMY_TRUST_FORWARDED_PROTO",
    "CSRF_TRUSTED_ORIGINS",
)


def _read_settings(**overrides: str) -> dict:
    env = os.environ.copy()
    for key in _OWNED_KEYS:
        env.pop(key, None)
    env.update(_PROD_BASE)
    env.update(overrides)
    script = (
        # Neutralize the repo .env auto-load so `env` is the only input. settings.py
        # does `from dotenv import load_dotenv` at import time, so rebinding the
        # attribute on the dotenv module here makes that import pick up the no-op.
        "import dotenv; dotenv.load_dotenv = lambda *a, **k: False;"
        "import json;"
        "from django.conf import settings;"
        "print(json.dumps({"
        "'ADMIN_ENABLED': settings.ADMIN_ENABLED,"
        "'SESSION_COOKIE_SECURE': settings.SESSION_COOKIE_SECURE,"
        "'CSRF_COOKIE_SECURE': settings.CSRF_COOKIE_SECURE,"
        "'CSRF_TRUSTED_ORIGINS': settings.CSRF_TRUSTED_ORIGINS,"
        "'SECURE_PROXY_SSL_HEADER': getattr(settings, 'SECURE_PROXY_SSL_HEADER', None),"
        "'DEBUG': settings.DEBUG}))"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


# --- ADMIN_ENABLED defaults to DJANGO_DEBUG, but an explicit flag wins (GAP#2) ------


def test_admin_enabled_defaults_to_debug_true():
    assert _read_settings(DJANGO_DEBUG="true")["ADMIN_ENABLED"] is True


def test_admin_enabled_defaults_to_debug_false():
    assert _read_settings(DJANGO_DEBUG="false")["ADMIN_ENABLED"] is False


def test_admin_can_be_explicitly_enabled_in_production():
    # DEBUG=false but the operator explicitly opts in.
    result = _read_settings(DJANGO_DEBUG="false", OCTONOMY_ADMIN_ENABLED="true")
    assert result["ADMIN_ENABLED"] is True


def test_admin_can_be_explicitly_disabled_in_debug():
    result = _read_settings(DJANGO_DEBUG="true", OCTONOMY_ADMIN_ENABLED="false")
    assert result["ADMIN_ENABLED"] is False


# --- Cookie-secure defaults to (not DEBUG), overridable by env (GAP#1) --------------


def test_cookies_secure_by_default_in_production():
    result = _read_settings(DJANGO_DEBUG="false")
    assert result["SESSION_COOKIE_SECURE"] is True
    assert result["CSRF_COOKIE_SECURE"] is True


def test_cookies_not_secure_by_default_in_debug():
    result = _read_settings(DJANGO_DEBUG="true")
    assert result["SESSION_COOKIE_SECURE"] is False
    assert result["CSRF_COOKIE_SECURE"] is False


@pytest.mark.parametrize("cookie", ["SESSION_COOKIE_SECURE", "CSRF_COOKIE_SECURE"])
def test_explicit_cookie_override_wins_over_debug_default(cookie):
    # DEBUG=false would default to True, but an explicit "false" override wins — the
    # escape hatch for a TLS-terminating proxy on a private network.
    result = _read_settings(DJANGO_DEBUG="false", **{cookie: "false"})
    assert result[cookie] is False


# --- TLS-terminating proxy: forwarded-scheme trust is opt-in and off by default -----


def test_forwarded_proto_not_trusted_by_default():
    # Off by default: trusting a spoofable header would downgrade HTTPS enforcement.
    assert _read_settings(DJANGO_DEBUG="false")["SECURE_PROXY_SSL_HEADER"] is None


def test_forwarded_proto_trusted_when_opted_in():
    result = _read_settings(DJANGO_DEBUG="false", OCTONOMY_TRUST_FORWARDED_PROTO="true")
    # JSON round-trips the tuple as a list.
    assert result["SECURE_PROXY_SSL_HEADER"] == ["HTTP_X_FORWARDED_PROTO", "https"]


# --- CSRF trusted origins are configurable from the environment (round-3 finding) ----


def test_csrf_trusted_origins_empty_by_default():
    assert _read_settings(DJANGO_DEBUG="false")["CSRF_TRUSTED_ORIGINS"] == []


def test_csrf_trusted_origins_parsed_and_stripped():
    result = _read_settings(
        DJANGO_DEBUG="false",
        CSRF_TRUSTED_ORIGINS="https://admin.example.com, https://ops.example.com",
    )
    assert result["CSRF_TRUSTED_ORIGINS"] == [
        "https://admin.example.com",
        "https://ops.example.com",
    ]


# --- Password validators harden the admin's only password surface (round-3 finding) --


def test_standard_password_validators_are_configured():
    from django.conf import settings

    names = {v["NAME"].rsplit(".", 1)[-1] for v in settings.AUTH_PASSWORD_VALIDATORS}
    assert {
        "UserAttributeSimilarityValidator",
        "MinimumLengthValidator",
        "CommonPasswordValidator",
        "NumericPasswordValidator",
    } <= names
