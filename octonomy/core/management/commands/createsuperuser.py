"""createsuperuser that enforces AUTH_PASSWORD_VALIDATORS on the non-interactive path.

Django validates the superuser password only on the *interactive* prompt path. The
non-interactive bootstrap — ``DJANGO_SUPERUSER_PASSWORD=... manage.py createsuperuser
--noinput``, the usual container/CI path — hands the password straight to
``create_superuser()`` without running the configured validators. Because the admin is
superuser-only and a superuser has platform-wide access, a weak bootstrap password is a
real risk, so this override runs the validators on that path too before delegating to
Django's command. The interactive path is unchanged (Django already validates it).

``octonomy.core`` is listed after ``django.contrib.auth`` in ``INSTALLED_APPS``, so this
command shadows the stock ``createsuperuser``.
"""

from __future__ import annotations

import os

from django.contrib.auth import get_user_model
from django.contrib.auth.management.commands import createsuperuser
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import CommandError

PASSWORD_ENV = "DJANGO_SUPERUSER_PASSWORD"


class Command(createsuperuser.Command):
    def handle(self, *args, **options):
        # Only the non-interactive path needs help; the interactive prompt already
        # validates. A weak password here means an active superuser with weak creds.
        if not options.get("interactive"):
            password = os.environ.get(PASSWORD_ENV)
            if password:
                self._validate_bootstrap_password(password, options)
        return super().handle(*args, **options)

    def _validate_bootstrap_password(self, password: str, options: dict) -> None:
        user = self._bootstrap_user_stub(options)
        try:
            validate_password(password, user)
        except ValidationError as error:
            raise CommandError(
                "The superuser password does not meet the password policy: "
                + " ".join(error.messages)
            ) from error

    def _bootstrap_user_stub(self, options: dict):
        """Unsaved user for UserAttributeSimilarityValidator, or None if unavailable.

        Resolves the username/required fields the same way Django's --noinput path does
        (option value, then DJANGO_SUPERUSER_<FIELD> env) so the similarity check has
        context. Falls back to ``None`` — the remaining validators (length, common,
        numeric) still run and catch the egregious cases.
        """

        user_model = get_user_model()
        fields: dict[str, str] = {}
        for field_name in (user_model.USERNAME_FIELD, *user_model.REQUIRED_FIELDS):
            value = options.get(field_name) or os.environ.get(
                f"DJANGO_SUPERUSER_{field_name.upper()}"
            )
            if value:
                fields[field_name] = value
        if not fields:
            return None
        try:
            return user_model(**fields)
        except (TypeError, ValueError):
            return None
