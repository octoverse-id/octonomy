"""Shared fixtures/helpers for the opt-in admin foundation tests."""

from __future__ import annotations

import contextlib
import importlib

import pytest
from django.test import override_settings
from django.urls import clear_url_caches


def _reload_urlconf() -> None:
    clear_url_caches()
    importlib.reload(importlib.import_module("config.urls"))


@contextlib.contextmanager
def admin_enabled(enabled: bool):
    """Rebuild ``config.urls`` with ``ADMIN_ENABLED`` set, restoring it afterward.

    ``config/urls.py`` decides whether to mount ``/admin/`` at import time from
    ``settings.ADMIN_ENABLED``, so toggling the flag requires reloading the urlconf.
    Reloading again on exit restores the session's real urlconf regardless of the
    ambient ``ADMIN_ENABLED``, keeping the toggle decoupled from ``DEBUG`` (GAP#2).
    """

    try:
        with override_settings(ADMIN_ENABLED=enabled):
            _reload_urlconf()
            yield
    finally:
        _reload_urlconf()


@pytest.fixture
def superuser(django_user_model):
    return django_user_model.objects.create_superuser(
        username="root", email="root@example.com", password="pw-root-123456"
    )


@pytest.fixture
def staff_user(django_user_model):
    """An ordinary staff account — passes Django's default gate but not ours."""

    return django_user_model.objects.create_user(
        username="staffer",
        email="staff@example.com",
        password="pw-staff-123456",
        is_staff=True,
    )


@pytest.fixture
def inactive_superuser(django_user_model):
    return django_user_model.objects.create_superuser(
        username="ghost",
        email="ghost@example.com",
        password="pw-ghost-123456",
        is_active=False,
    )
