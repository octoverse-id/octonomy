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
#
# Both values are 18 characters, and this dict feeds EVERY DJANGO_DEBUG=false subprocess
# in this file. They boot only because the boot guard judges emptiness and the local-dev
# literal, never length or entropy — the deliberate ceiling recorded as CFG-2 in TODOS.md
# and marked `gstack-shortcut(dec-584a8f2c)` on both guards in config/settings.py.
#
# A length or strength rule therefore invalidates this baseline and takes nearly the whole
# file with it. Measured against a `len(...) < 50` rule on both guards: every DEBUG=false
# case fails but one. The cases expecting a successful boot fail outright; the STATIC_URL
# and FORCE_SCRIPT_NAME rejection cases fail on the wrong message, because the secret guard
# raises before the checks they target. The lone survivor is
# test_empty_required_secret_refuses_to_boot[DJANGO_SECRET_KEY], which fails closed either
# way — and only while the hardened guard keeps today's message text. That breakage is by
# design: it is the tripwire that puts whoever lengthens these two values in front of the
# documentation #148 narrowed to describe the weaker guard.
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
    "OCTONOMY_STATIC_URL",
    "OCTONOMY_FORCE_SCRIPT_NAME",
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
        "'FORCE_SCRIPT_NAME': settings.FORCE_SCRIPT_NAME,"
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


# --- Required secrets fail closed when empty, not just when left at the default --------


def _import_settings(**overrides: str) -> subprocess.CompletedProcess:
    """Import config.settings in a fresh process; capture whether the boot guards fired."""
    env = os.environ.copy()
    for key in _OWNED_KEYS:
        env.pop(key, None)
    env.update(_PROD_BASE)
    env.update(overrides)
    script = (
        "import dotenv; dotenv.load_dotenv = lambda *a, **k: False;"
        # Accessing any setting forces config.settings to import, running the module-level
        # boot guards.
        "from django.conf import settings; settings.DEBUG"
    )
    return subprocess.run([sys.executable, "-c", script], env=env, capture_output=True, text=True)


@pytest.mark.parametrize(
    "var, message",
    [
        ("DJANGO_SECRET_KEY", "DJANGO_SECRET_KEY must be set to a non-default value"),
        ("SERVICE_TOKEN_PEPPER", "SERVICE_TOKEN_PEPPER must be set to a non-default value"),
    ],
)
def test_empty_required_secret_refuses_to_boot(var, message):
    # An EMPTY value must fail closed exactly like the local-dev default, so a half-filled
    # production env cannot boot on a blank signing key or unpeppered token hashes. The
    # container entrypoint runs plain `manage.py check`, which imports settings, so this
    # import-time guard is what actually protects the deploy.
    result = _import_settings(DJANGO_DEBUG="false", **{var: ""})
    assert result.returncode != 0, f"empty {var} should refuse to boot, but import succeeded"
    assert message in result.stderr


# --- Accepted ceiling: the guard judges emptiness, not strength (#147 approach C) ------


@pytest.mark.parametrize("var", ["DJANGO_SECRET_KEY", "SERVICE_TOKEN_PEPPER"])
def test_whitespace_only_secret_is_accepted(var):
    # CHARACTERIZATION, not an endorsement. It pins the ceiling #147 chose to accept —
    # documentation narrowed (#148) instead of the guard widened — so that hardening the
    # guard later is a deliberate act that updates the docs in the same commit. When
    # TODOS.md CFG-2 lands, this test flips to asserting a refusal.
    #
    # Scope: this describes the SETTINGS guard only. `not SECRET_KEY` is False for " ",
    # because a whitespace string is truthy. It says NOTHING about the .env template:
    # python-dotenv strips an UNQUOTED trailing " " to "", which the guard DOES reject
    # (the test above), so the documented unquoted .env style cannot reach here. Only a
    # value that survives into the process environment intact does — a QUOTED `KEY=" "`
    # in .env or a systemd EnvironmentFile, a Kubernetes Secret `stringData` entry such as
    # `DJANGO_SECRET_KEY: " "`, or `docker run -e KEY=" "`. This harness disables
    # load_dotenv, so it exercises the process environment directly, which is the surface
    # those channels share.
    result = _import_settings(DJANGO_DEBUG="false", **{var: " "})
    assert result.returncode == 0, (
        f"whitespace-only {var} is expected to boot today (TODOS.md CFG-2); if the guard "
        f"now fails closed, harden the docs claim sites in the same commit: {result.stderr}"
    )


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


# --- Subpath deployment settings are validated at import (#143) ----------------------
#
# Both are derived at settings-import time and both refuse to boot on a malformed value,
# so they need the same hermetic subprocess treatment as the secrets above.


@pytest.mark.parametrize(
    "value",
    [
        # Relative: would silently restore the first-read script-prefix caching that the
        # absolute default exists to remove.
        "static/",
        "octonomy/static/",
    ],
)
def test_relative_static_url_refuses_to_boot(value):
    result = _import_settings(DJANGO_DEBUG="false", OCTONOMY_STATIC_URL=value)

    assert result.returncode != 0, f"{value!r} should refuse to boot"
    assert "OCTONOMY_STATIC_URL must be root-absolute" in result.stderr


@pytest.mark.parametrize("value", ["/octonomy/static/", "https://cdn.example.com/static/"])
def test_absolute_static_url_is_accepted(value):
    # Root-absolute for a subpath, and a full URL for a CDN — both legitimate.
    assert _import_settings(DJANGO_DEBUG="false", OCTONOMY_STATIC_URL=value).returncode == 0


@pytest.mark.parametrize(
    "value",
    [
        # Django prepends FORCE_SCRIPT_NAME to every reverse() result verbatim, so a value
        # carrying a host makes the admin login form POST the operator's password
        # off-site. Refusing to boot is the only acceptable response to each of these.
        "https://evil.example",
        "http://evil.example/octonomy",
        "//evil.example",
        "octonomy",
        "/octonomy?next=x",
        "/octonomy#frag",
    ],
)
def test_malformed_force_script_name_refuses_to_boot(value):
    result = _import_settings(DJANGO_DEBUG="false", OCTONOMY_FORCE_SCRIPT_NAME=value)

    assert result.returncode != 0, f"{value!r} should refuse to boot"
    assert "OCTONOMY_FORCE_SCRIPT_NAME must be a local absolute path" in result.stderr


@pytest.mark.parametrize("value", ["/octonomy", "/octonomy/", "/a/b"])
def test_local_absolute_force_script_name_is_accepted(value):
    assert _import_settings(DJANGO_DEBUG="false", OCTONOMY_FORCE_SCRIPT_NAME=value).returncode == 0


def test_force_script_name_is_unset_by_default():
    # Django's own default. Every non-subpath deployment must be untouched by this.
    assert _read_settings(DJANGO_DEBUG="false")["FORCE_SCRIPT_NAME"] is None
