"""Unfold-themed User/Group admin, wired up after autodiscover.

Imported lazily from ``OctonomyAdminConfig.ready()`` (``config/admin.py``) — never at
module-import time — because it imports ``django.contrib.auth.admin`` and
``unfold.admin``, both of which touch the app registry and would trigger a circular
import through ``default_site`` resolution if pulled in from ``config/adminsite.py``.
"""

from __future__ import annotations

from django.contrib import admin
from django.contrib.auth.admin import GroupAdmin as BaseGroupAdmin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import Group, User
from unfold.admin import ModelAdmin as UnfoldModelAdmin
from unfold.forms import AdminPasswordChangeForm, UserChangeForm, UserCreationForm


class OctonomyUserAdmin(BaseUserAdmin, UnfoldModelAdmin):
    """Django's ``UserAdmin`` rendered with Unfold-compatible auth forms."""

    form = UserChangeForm
    add_form = UserCreationForm
    change_password_form = AdminPasswordChangeForm


class OctonomyGroupAdmin(BaseGroupAdmin, UnfoldModelAdmin):
    """Django's ``GroupAdmin`` themed with Unfold."""


def reregister_auth_models() -> None:
    """Swap the stock User/Group admins for Unfold-themed ones.

    ``admin.autodiscover()`` (run in ``AdminConfig.ready``) has already registered the
    stock ``django.contrib.auth`` admins on our site, so unregister first, then
    re-register with the Unfold-compatible classes. Idempotent.
    """

    for model, admin_class in ((User, OctonomyUserAdmin), (Group, OctonomyGroupAdmin)):
        if admin.site.is_registered(model):
            admin.site.unregister(model)
        admin.site.register(model, admin_class)
