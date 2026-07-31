"""The Octonomy admin site: an opt-in, superuser-only operator console.

The admin is a *thin, optional* surface over the headless REST taxonomy service,
themed with django-unfold and gated two independent ways:

1. It is mounted at ``/admin/`` only when ``settings.ADMIN_ENABLED`` is true
   (``config/urls.py``); when disabled the route is simply absent (a resolver 404).
2. Even when mounted, the whole site is restricted to *active superusers*
   (``has_permission`` below). Django's default admin gate only requires
   ``is_active and is_staff``; we deliberately tighten it so an ordinary staff account
   cannot enter or inspect any model. One gate on the site covers every model.

We use ``unfold.apps.BasicAppConfig`` in ``INSTALLED_APPS`` (not unfold's default app
config, which swaps ``admin.site`` for a *bare* ``UnfoldAdminSite`` and would drop this
``has_permission`` override), and point Django's default admin site here via
``OctonomyAdminConfig.default_site`` (``config/admin.py``).

Kept deliberately minimal: this module must import ONLY the unfold site base, never
``django.contrib.auth.admin``. Importing auth's admin re-enters this module through
``default_site`` resolution (a circular import) before ``OctonomyAdminSite`` exists.
The Unfold-themed User/Group admins therefore live in ``config/auth_admin.py``, wired
up from ``OctonomyAdminConfig.ready()`` after autodiscover has run.
"""

from __future__ import annotations

from django.http import HttpRequest
from unfold.sites import UnfoldAdminSite


class OctonomyAdminSite(UnfoldAdminSite):
    """Superuser-only admin site.

    Branding (title/header/subheader and the Swagger "view site" link) is driven by
    the ``UNFOLD`` settings dict in ``config/settings.py``.
    """

    def has_permission(self, request: HttpRequest) -> bool:
        # Tighter than Django's default (is_active AND is_staff): this operator
        # console is platform-wide and superuser-only. There is no tenant-scoped
        # staff tier in this epic.
        user = getattr(request, "user", None)
        return bool(user is not None and user.is_active and user.is_superuser)
