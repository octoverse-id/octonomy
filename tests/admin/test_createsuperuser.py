"""Lock the createsuperuser override (round-4 finding).

Django validates the superuser password only on the interactive path; the
non-interactive bootstrap (``DJANGO_SUPERUSER_PASSWORD`` + ``--noinput``) skips it. The
octonomy.core override runs AUTH_PASSWORD_VALIDATORS on that path too. These tests also
guard the INSTALLED_APPS ordering: command overrides resolve to the earliest app, so if
octonomy.core ever slips after django.contrib.auth the override stops winning and
`test_override_wins_command_resolution` (and the weak-password cases) fail.
"""

from __future__ import annotations

from io import StringIO
from unittest import mock

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command, get_commands
from django.core.management.base import CommandError

pytestmark = pytest.mark.django_db


def _run_interactive(password: str, username: str) -> None:
    """Drive the interactive createsuperuser flow with mocked TTY prompts.

    Django skips interactive creation unless ``stdin.isatty()`` is true; username and
    required fields are read via ``input()`` and the password via ``getpass``.
    """

    stdin = mock.MagicMock()
    stdin.isatty.return_value = True
    with (
        mock.patch("builtins.input", side_effect=[username, f"{username}@example.com"]),
        mock.patch("getpass.getpass", return_value=password),
    ):
        call_command(
            "createsuperuser",
            interactive=True,
            stdin=stdin,
            stdout=StringIO(),
            stderr=StringIO(),
        )


def test_override_wins_command_resolution():
    assert get_commands()["createsuperuser"] == "octonomy.core"


def test_noinput_rejects_weak_password(monkeypatch):
    monkeypatch.setenv("DJANGO_SUPERUSER_PASSWORD", "1")
    with pytest.raises(CommandError, match="password policy"):
        call_command("createsuperuser", interactive=False, username="weakling", email="w@e.com")
    assert not get_user_model().objects.filter(username="weakling").exists()


def test_noinput_rejects_entirely_numeric_password(monkeypatch):
    # Long enough to pass MinimumLength, but NumericPasswordValidator rejects it.
    monkeypatch.setenv("DJANGO_SUPERUSER_PASSWORD", "48151623")
    with pytest.raises(CommandError, match="password policy"):
        call_command("createsuperuser", interactive=False, username="numguy", email="n@e.com")
    assert not get_user_model().objects.filter(username="numguy").exists()


def test_noinput_accepts_strong_password(monkeypatch):
    monkeypatch.setenv("DJANGO_SUPERUSER_PASSWORD", "Str0ng-Op3rator-Pass!")
    call_command("createsuperuser", interactive=False, username="strongboot", email="s@e.com")
    user = get_user_model().objects.get(username="strongboot")
    assert user.is_superuser and user.is_active
    assert user.check_password("Str0ng-Op3rator-Pass!")


def test_noinput_without_password_is_left_to_django(monkeypatch):
    # No DJANGO_SUPERUSER_PASSWORD => Django creates an unusable-password superuser
    # (must set one later). Our validation only guards a provided password.
    monkeypatch.delenv("DJANGO_SUPERUSER_PASSWORD", raising=False)
    call_command("createsuperuser", interactive=False, username="nopass", email="np@e.com")
    user = get_user_model().objects.get(username="nopass")
    assert user.is_superuser and not user.has_usable_password()


# --- Interactive path: the "[y/N] bypass" escape hatch is closed --------------------


def test_interactive_weak_password_aborts_without_bypass():
    # Stock Django would print the policy errors then offer to create the user anyway;
    # our override makes the failure fatal, so the command aborts and creates no user.
    with pytest.raises(CommandError, match="password policy"):
        _run_interactive("1", "weakint")
    assert not get_user_model().objects.filter(username="weakint").exists()


def test_interactive_strong_password_succeeds():
    _run_interactive("Str0ng-Op3rator-Pass!", "strongint")
    user = get_user_model().objects.get(username="strongint")
    assert user.is_superuser and user.is_active


def test_interactive_patch_is_restored_after_run():
    # The interactive fatal-validation swap must not leak into the wider process.
    from django.contrib.auth.management.commands import createsuperuser as django_cmd
    from django.contrib.auth.password_validation import (
        validate_password as real_validate_password,
    )

    _run_interactive("Str0ng-Op3rator-Pass!", "restorecheck")
    assert django_cmd.validate_password is real_validate_password
