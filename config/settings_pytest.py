"""Settings module pytest runs against — ``config.settings`` with one override.

Wired via ``DJANGO_SETTINGS_MODULE`` in ``[tool.pytest.ini_options]``. Everything is
inherited from the real settings; only the staticfiles storage backend differs, and the
reason is recorded below. Nothing product-shaped belongs here — a divergence between
what tests exercise and what production runs is precisely the class of gap that let
issue #142 ship, so keep this file at one deliberate, documented exception.

Named ``settings_pytest`` rather than ``settings_test``/``test_settings`` so it cannot be
swept up by pytest's ``python_files`` globs (``test_*.py``, ``*_test.py``) if anyone ever
points a collection run at ``config/``.
"""

from __future__ import annotations

from config.settings import *  # noqa: F403
from config.settings import STORAGES as _PRODUCTION_STORAGES

# Production runs WhiteNoise's hashed CompressedManifestStaticFilesStorage. Under pytest
# that backend fails 39 of the admin tests: pytest-django forces DEBUG=False, so
# HashedFilesMixin._url skips its DEBUG bypass and resolves every {% static %} through
# staticfiles.json — a file that only `collectstatic` writes, into a gitignored
# STATIC_ROOT that CI and every fresh clone start without. The render then raises
# "Missing staticfiles manifest entry for 'unfold/fonts/inter/styles.css'" and the admin
# 500s. WHITENOISE_MANIFEST_STRICT=False does not help: it falls through to
# hashed_name(), which still needs the file present in STATIC_ROOT.
#
# gstack-shortcut(dec-805139c7): the suite's DEFAULT staticfiles backend is the plain one,
# so a Missing-staticfiles-manifest regression in a page that only the wider admin tests
# render will ship green; upgrade when the first manifest-related production incident
# occurs, or CI goes green and a deployed image still 500s on static — then adopt a
# session-scoped collectstatic fixture in tests/conftest.py instead.
#
# Narrowed from the original decision, which accepted that NO test would exercise the
# manifest backend: tests/admin/test_static_serving.py collects a real STATIC_ROOT under
# the production STORAGES and asserts the admin login page, the browsable API page and a
# hashed asset URL all work through it. What is still uncovered is every OTHER admin page
# the suite renders (changelists, forms, diagnostics) — those go through plain storage and
# would not notice a manifest break.
#
# #144 is the planned additional control: a CI job that boots the real image and fetches a
# hashed URL out of rendered admin HTML. It is not in place yet.
STORAGES = {
    **_PRODUCTION_STORAGES,
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}
