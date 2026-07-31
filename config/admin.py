"""Admin app config for the opt-in, superuser-only Octonomy operator console.

This module is imported during app-registry population (it is referenced from
``INSTALLED_APPS``), so it must NOT import models, the admin, or unfold at top level
— doing so raises ``AppRegistryNotReady``. The pieces are imported lazily:

* ``default_site`` is a dotted path Django resolves *after* apps are ready. It points
  at ``OctonomyAdminSite`` in ``config/adminsite.py`` (site + superuser-only gate).
* ``ready()`` imports the User/Group re-registration helper from
  ``config/auth_admin.py`` only once autodiscover has run.
"""

from __future__ import annotations

from django.contrib.admin.apps import AdminConfig


class OctonomyAdminConfig(AdminConfig):
    """Replaces ``django.contrib.admin``; installs ``OctonomyAdminSite`` as default."""

    default_site = "config.adminsite.OctonomyAdminSite"

    def ready(self) -> None:
        # super().ready() registers the admin system checks and runs
        # admin.autodiscover(), which registers the stock django.contrib.auth
        # admins on our site. Only after that can we re-theme User/Group.
        super().ready()
        from config.auth_admin import reregister_auth_models

        reregister_auth_models()
