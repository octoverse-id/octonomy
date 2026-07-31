"""createsuperuser that always enforces AUTH_PASSWORD_VALIDATORS.

Stock Django leaves two ways to bootstrap a superuser whose password violates the
configured policy — both relevant because the admin is superuser-only and a superuser
has platform-wide access:

* **Non-interactive** (``DJANGO_SUPERUSER_PASSWORD=... createsuperuser --noinput``, the
  usual container/CI path): Django hands the password straight to ``create_superuser``
  without validating it at all.
* **Interactive**: Django validates, but on failure offers a ``Bypass password
  validation and create user anyway? [y/N]`` escape hatch that creates the weak account
  if the operator answers ``y``.

This override closes both. The non-interactive path is pre-validated before delegating.
For the interactive path, the module-level ``validate_password`` Django's prompt loop
calls is swapped for one that raises a *fatal* ``CommandError`` instead of the catchable
``ValidationError`` Django would trap and then wave through — so a policy-violating
password aborts the command rather than reaching the bypass prompt.

``octonomy.core`` is listed before ``django.contrib.auth`` in ``INSTALLED_APPS`` so this
command shadows the stock one (command overrides resolve to the earliest app). That
ordering is load-bearing and locked by tests/admin/test_createsuperuser.py.
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
        # Non-interactive: Django never validates the env-supplied password, so do it
        # here before delegating (the interactive patch below is inert on this path —
        # Django's --noinput branch does not call validate_password).
        if not options.get("interactive"):
            password = os.environ.get(PASSWORD_ENV)
            if password:
                self._reject_if_weak(password, self._bootstrap_user_stub(options))

        # Interactive: make the validator failure fatal so Django's "[y/N] bypass"
        # prompt is never reached. Restored unconditionally.
        original = createsuperuser.validate_password
        createsuperuser.validate_password = self._fatal_validate_password
        try:
            return super().handle(*args, **options)
        finally:
            createsuperuser.validate_password = original

    @staticmethod
    def _fatal_validate_password(password, user=None, password_validators=None):
        Command._reject_if_weak(password, user, password_validators)

    @staticmethod
    def _reject_if_weak(password, user=None, password_validators=None):
        try:
            validate_password(password, user, password_validators)
        except ValidationError as error:
            raise CommandError(
                "The superuser password does not meet the password policy: "
                + " ".join(error.messages)
            ) from error

    def _bootstrap_user_stub(self, options: dict):
        """Unsaved user for UserAttributeSimilarityValidator, or None if unavailable.

        Resolves the username/required fields the way Django's --noinput path does
        (option value, then ``DJANGO_SUPERUSER_<FIELD>`` env) so the similarity check
        has context. Falls back to ``None`` — the remaining validators (length, common,
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
