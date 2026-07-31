"""Env-driven settings derivation for the admin foundation (#84, plan GAP#1/#2).

These settings (ADMIN_ENABLED, SESSION_COOKIE_SECURE, CSRF_COOKIE_SECURE) are computed
once at settings-import time from the environment, so ``override_settings`` cannot
exercise the derivation. Each case re-imports ``config.settings`` in a fresh process
with a controlled environment and reads the resulting value back as JSON.
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
)


def _read_settings(**overrides: str) -> dict:
    env = os.environ.copy()
    for key in _OWNED_KEYS:
        env.pop(key, None)
    env.update(_PROD_BASE)
    env.update(overrides)
    script = (
        "import json;"
        "from django.conf import settings;"
        "print(json.dumps({"
        "'ADMIN_ENABLED': settings.ADMIN_ENABLED,"
        "'SESSION_COOKIE_SECURE': settings.SESSION_COOKIE_SECURE,"
        "'CSRF_COOKIE_SECURE': settings.CSRF_COOKIE_SECURE,"
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
