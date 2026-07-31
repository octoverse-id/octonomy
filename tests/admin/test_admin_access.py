"""Access-control and rendering tests for the opt-in Unfold admin (#84).

Foundation only: the site is mounted, superuser-gated, and Unfold-themed. No taxonomy
models are registered yet (that is #85), so these tests exercise the site itself and
the re-themed User/Group auth admin.
"""

from __future__ import annotations

import pytest
from django.contrib import admin
from django.contrib.auth.models import Group, User
from django.test import Client
from django.urls import set_script_prefix

from config.adminsite import OctonomyAdminSite, admin_site_url
from config.auth_admin import OctonomyGroupAdmin, OctonomyUserAdmin
from tests.admin.conftest import admin_enabled

pytestmark = pytest.mark.django_db


def test_admin_absent_when_disabled(client):
    # Disabled => the route is not registered at all, so this 404s at the resolver
    # rather than rendering a branded denial page.
    with admin_enabled(False):
        response = client.get("/admin/")
    assert response.status_code == 404


def test_admin_anonymous_redirects_to_login(client):
    with admin_enabled(True):
        response = client.get("/admin/")
    assert response.status_code == 302
    assert response.headers["Location"].startswith("/admin/login/")


def test_active_superuser_can_enter(client, superuser):
    client.force_login(superuser)
    with admin_enabled(True):
        response = client.get("/admin/")
    assert response.status_code == 200
    # Unfold renders the branded header from the UNFOLD settings dict.
    assert b"Octonomy Admin" in response.content


def test_ordinary_staff_cannot_enter(client, staff_user):
    # is_staff is enough for Django's default gate but NOT for ours (superuser-only).
    client.force_login(staff_user)
    with admin_enabled(True):
        response = client.get("/admin/")
    assert response.status_code == 302
    assert response.headers["Location"].startswith("/admin/login/")


def test_inactive_superuser_cannot_enter(client, inactive_superuser):
    client.force_login(inactive_superuser)
    with admin_enabled(True):
        response = client.get("/admin/")
    assert response.status_code == 302
    assert response.headers["Location"].startswith("/admin/login/")


def test_gate_is_site_wide_not_just_the_index(client, staff_user):
    # The gate lives on the site (admin_view wraps every view), not only the index.
    # Prove a staff user is bounced from a concrete model page too, so a future model
    # registration cannot accidentally expose itself to non-superusers.
    client.force_login(staff_user)
    with admin_enabled(True):
        response = client.get("/admin/auth/user/")
    assert response.status_code == 302
    assert response.headers["Location"].startswith("/admin/login/")


def test_the_default_site_is_octonomys_superuser_only_site():
    # The lazy admin.site proxy resolves to our custom site — so every model that
    # registers on the default site inherits the superuser-only gate.
    assert isinstance(admin.site, OctonomyAdminSite)


def test_login_page_renders_unfold_and_is_csrf_protected():
    # A dedicated client with CSRF enforcement on (the default test client disables it).
    csrf_client = Client(enforce_csrf_checks=True)
    with admin_enabled(True):
        get_response = csrf_client.get("/admin/login/")
        post_response = csrf_client.post(
            "/admin/login/", {"username": "root", "password": "pw-root-123456"}
        )
    body = get_response.content.decode()
    assert get_response.status_code == 200
    assert "csrfmiddlewaretoken" in body  # CSRF token is rendered into the form
    assert "/static/unfold/" in body  # Unfold static assets are referenced
    assert "Octonomy Admin" in body  # Unfold branding
    # No CSRF token supplied => CsrfViewMiddleware rejects the POST.
    assert post_response.status_code == 403


def test_user_and_group_use_unfold_admin(client, superuser):
    # Registered with the Unfold-compatible admin classes, not the stock ones.
    assert isinstance(admin.site._registry[User], OctonomyUserAdmin)
    assert isinstance(admin.site._registry[Group], OctonomyGroupAdmin)

    client.force_login(superuser)
    with admin_enabled(True):
        user_list = client.get("/admin/auth/user/")
        user_add = client.get("/admin/auth/user/add/")
        group_list = client.get("/admin/auth/group/")
    assert user_list.status_code == 200
    assert user_add.status_code == 200
    assert group_list.status_code == 200
    # Themed rendering: Unfold assets load on the auth admin pages.
    assert "/static/unfold/" in user_list.content.decode()


def test_site_url_reverses_swagger_and_honors_script_prefix():
    # The admin header/home link must not hardcode the host-root path: under a WSGI
    # SCRIPT_NAME subpath mount it has to carry the prefix, like the rest of the app.
    set_script_prefix("/")
    try:
        assert admin_site_url(None) == "/api/docs/swagger/"
        set_script_prefix("/octonomy/")
        assert admin_site_url(None) == "/octonomy/api/docs/swagger/"
    finally:
        set_script_prefix("/")


def test_admin_index_renders_swagger_site_link(client, superuser):
    client.force_login(superuser)
    with admin_enabled(True):
        response = client.get("/admin/")
    assert response.status_code == 200
    # Unfold resolves UNFOLD["SITE_URL"] (the dotted callable) into the page.
    assert "/api/docs/swagger/" in response.content.decode()
