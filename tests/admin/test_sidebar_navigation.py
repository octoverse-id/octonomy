"""Grouped Unfold sidebar navigation (UNFOLD["SIDEBAR"]).

The sidebar is curated into domain groups (Taxonomy / Diagnostics / Access) with
``show_all_applications`` off, so it shows ONLY this navigation. That makes coverage a
correctness property: every model registered on the admin must have a link here, or it
becomes unreachable from the menu.
"""

from __future__ import annotations

import pytest
from django.conf import settings
from django.contrib import admin
from django.urls import reverse

from tests.admin.conftest import admin_enabled

pytestmark = pytest.mark.django_db


def test_sidebar_renders_grouped_headings(client, superuser):
    client.force_login(superuser)
    with admin_enabled(True):
        response = client.get("/admin/")
    body = response.content.decode()
    assert response.status_code == 200
    for heading in ("Taxonomy", "Diagnostics", "Access"):
        assert heading in body
    for item in ("Vocabularies", "Tag aliases", "Audit logs", "Service client grants"):
        assert item in body


def test_site_dropdown_renders_repo_and_api_doc_links(client, superuser):
    client.force_login(superuser)
    with admin_enabled(True):
        response = client.get("/admin/")
    body = response.content.decode()
    assert response.status_code == 200
    for title in ("GitHub repository", "Swagger API docs", "ReDoc API docs"):
        assert title in body
    assert "https://github.com/octoverse-id/octonomy" in body
    assert reverse("swagger-ui") in body
    assert reverse("redoc") in body


def test_sidebar_navigation_covers_every_registered_model():
    # show_all_applications is off, so a model missing from the navigation is unreachable
    # from the sidebar. Guard every registered model's changelist link.
    with admin_enabled(True):
        linked = {
            str(item["link"])
            for group in settings.UNFOLD["SIDEBAR"]["navigation"]
            for item in group["items"]
        }
        expected = {
            reverse(f"admin:{model._meta.app_label}_{model._meta.model_name}_changelist")
            for model in admin.site._registry
        }
    missing = expected - linked
    assert not missing, f"registered models missing from the sidebar navigation: {missing}"
