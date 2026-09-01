"""The app serves its own bundled static assets (#143, epic #142).

Before this, nothing in the Octonomy process answered ``/static/*``: with
``DEBUG=false`` every admin and browsable-API asset 404'd on all three production
channels while the files sat unreachable inside the image. ``WhiteNoiseMiddleware``
(``config/settings.py``, index 1) closes that.

These tests live under ``tests/admin/`` because the admin epic surfaced the bug and the
admin fixtures are here — but static is deliberately **not** admin-only, which is why
``test_browsable_api_*`` below assert the DRF surface too: ``DEFAULT_RENDERER_CLASSES``
includes ``BrowsableAPIRenderer``, so ``/static/rest_framework/*`` is needed even when
the console is off. That is the reason the middleware is unconditional.

Two static roots are exercised, both built by the real ``collectstatic``:

* ``collected_static`` — the plain, non-hashed backend the suite runs on (see
  ``config/settings_pytest.py``).
* ``production_static`` — the real ``STORAGES`` from ``config.settings``, i.e. hashed
  manifest storage. It narrows, but does not close, the ``dec-805139c7`` ceiling: every
  other admin page the suite renders still goes through plain storage, so #144's planned
  image assertion is still worth having.

Both are imported from their source of truth rather than copied, so a change to either
settings module moves these tests with it instead of leaving them testing the old one.
"""

from __future__ import annotations

import logging

import pytest
from django.contrib.staticfiles.storage import staticfiles_storage
from django.core.management import call_command
from django.test import Client, override_settings

from config.settings import STORAGES as PRODUCTION_STORAGES
from config.settings_pytest import STORAGES as PLAIN_STORAGES
from tests.admin.conftest import admin_enabled

# A year, in seconds. WhiteNoise actually stamps ten years on a content-addressed
# filename; assert the floor rather than its constant so an upstream retune of
# `whitenoise.base.WhiteNoise.FOREVER` does not fail this for no reason.
ONE_YEAR = 31_536_000

UNHASHED_ADMIN_CSS_URL = "/static/admin/css/base.css"


def _collect_once(tmp_path_factory, name, storages):
    """Return a STATIC_ROOT populated by a real ``collectstatic`` under ``storages``.

    Only the collection is module-scoped — ``collectstatic`` costs ~1s and every test
    here wants the same output. The settings override is entered just long enough to run
    the command and is then released, because a module-scoped override would stay active
    for the rest of the module and silently decide which backend and root the *later*
    tests exercise. Activation is the function-scoped fixtures' job below.
    """

    root = tmp_path_factory.mktemp(name)
    with override_settings(STATIC_ROOT=str(root), STORAGES=storages):
        call_command("collectstatic", "--noinput", verbosity=0)
    return root


@pytest.fixture(scope="module")
def _plain_static_root(tmp_path_factory):
    return _collect_once(tmp_path_factory, "staticfiles", PLAIN_STORAGES)


@pytest.fixture(scope="module")
def _manifest_static_root(tmp_path_factory):
    return _collect_once(tmp_path_factory, "staticfiles-manifest", PRODUCTION_STORAGES)


@pytest.fixture
def collected_static(_plain_static_root):
    """Activate the plain-backend root for one test, and prove which backend is live."""

    with override_settings(STATIC_ROOT=str(_plain_static_root), STORAGES=PLAIN_STORAGES):
        assert staticfiles_storage.url("admin/css/base.css") == UNHASHED_ADMIN_CSS_URL, (
            "expected the plain (non-hashed) backend to be active"
        )
        yield _plain_static_root


@pytest.fixture
def production_static(_manifest_static_root):
    """Activate the real production STORAGES for one test, and prove they are live.

    The assertion is the point: without it a render test could pass under the plain
    backend and be mistaken for manifest coverage.
    """

    with override_settings(STATIC_ROOT=str(_manifest_static_root), STORAGES=PRODUCTION_STORAGES):
        assert staticfiles_storage.url("admin/css/base.css") != UNHASHED_ADMIN_CSS_URL, (
            "expected the hashed manifest backend to be active"
        )
        yield _manifest_static_root


# --- The exact probes that 404'd before this change ---------------------------------


def test_admin_css_is_served(collected_static):
    # T-A1. `GET /static/admin/css/base.css -> 404 (Resolver404, no route)` was the
    # reproduction in #142; it must now be answered by the app itself.
    response = Client().get("/static/admin/css/base.css")

    assert response.status_code == 200
    assert response.getvalue()


def test_served_asset_carries_its_real_content_type(collected_static):
    # T-A2. A 200 with `application/octet-stream` still breaks rendering, so the status
    # code alone is not the guarantee that matters to a browser.
    response = Client().get("/static/admin/css/base.css")

    assert response.headers["Content-Type"].startswith("text/css")


def test_browsable_api_asset_is_served(collected_static):
    # T-B2. The browsable API is not optional — this proves the fix is not admin-only.
    response = Client().get("/static/rest_framework/css/bootstrap.min.css")

    assert response.status_code == 200


def test_unfold_asset_is_served(collected_static):
    # T-C2. Unfold ships ~1.6 MB of its own assets; the console is unusable without them.
    response = Client().get("/static/unfold/css/styles.css")

    assert response.status_code == 200


def test_unknown_static_path_is_a_404_not_a_500(collected_static):
    # T-A5. WhiteNoise must hand an unmatched path back to Django's resolver, which has
    # no /static/ route — so this is an ordinary 404, never an exception.
    client = Client()

    # Prove WhiteNoise is actually live first, otherwise the 404 below is vacuous: with no
    # middleware at all, every /static/ path 404s and this test passes for the wrong reason.
    assert client.get("/static/admin/css/base.css").status_code == 200

    assert client.get("/static/does-not-exist.css").status_code == 404


# --- Header contract -----------------------------------------------------------------


def test_static_response_has_no_wildcard_cors_header(collected_static):
    # WHITENOISE_ALLOW_ALL_ORIGINS is set to False in config/settings.py. WhiteNoise
    # defaults it to True, which would stamp `Access-Control-Allow-Origin: *` on every
    # asset; these are same-origin first-party files with no cross-origin consumer.
    response = Client().get("/static/admin/css/base.css")

    # Assert the 200 first: a 404 would satisfy the header assertion vacuously.
    assert response.status_code == 200
    assert "Access-Control-Allow-Origin" not in response.headers


def test_hashed_url_is_cached_immutably(production_static):
    # T-A4. Only content-addressed names may be cached forever, and only the manifest
    # backend produces them — so this test needs the production STORAGES, not the
    # suite's plain one. WhiteNoise decides via `immutable_file_test`, which round-trips
    # the name through staticfiles_storage.url(); under plain storage it correctly
    # refuses, and the asset would get the short 60s max-age instead.
    url = staticfiles_storage.url("admin/css/base.css")

    response = Client().get(url)

    assert response.status_code == 200
    cache_control = response.headers["Cache-Control"]
    assert "immutable" in cache_control
    max_age = int(cache_control.split("max-age=")[1].split(",")[0])
    assert max_age >= ONE_YEAR


# --- The observability trade, made explicit ------------------------------------------


def test_static_request_is_not_request_logged_but_a_real_request_still_is(collected_static, caplog):
    # T-A6. WhiteNoise sits at index 1 and short-circuits, so a static hit never reaches
    # RequestContextMiddleware (last) and emits no `octonomy.requests` line. That is the
    # deliberate cost of the placement — roughly 25 dropped lines per admin page load —
    # and it must not extend to real traffic, which is the half that matters.
    caplog.set_level(logging.INFO, logger="octonomy.requests")
    client = Client()

    client.get("/static/admin/css/base.css")
    assert [r for r in caplog.records if r.message == "request_completed"] == []

    client.get("/health/live")
    assert [r for r in caplog.records if r.message == "request_completed"]


# --- Pages render under the real (manifest) backend -----------------------------------
#
# Order-independent: `production_static` activates its settings for one test only, so
# these can sit anywhere in the file.
#
# Mandatory, and the reason `production_static` exists at all: with manifest storage
# `{% static %}` raises `ValueError: Missing staticfiles manifest entry` at render time
# for any asset that was not collected, which surfaces as a 500 rather than an unstyled
# page. A template referencing an uncollected asset is therefore a render-time break,
# not a cosmetic one.


def test_admin_login_page_renders_under_manifest_storage(production_static):
    # T-C1.
    with admin_enabled(True):
        response = Client().get("/admin/login/")

    assert response.status_code == 200


@pytest.mark.django_db
def test_browsable_api_page_renders_under_manifest_storage(production_static, service_token):
    # T-B1. Accept: text/html selects BrowsableAPIRenderer, whose template pulls in
    # /static/rest_framework/*.
    client = Client()
    response = client.get(
        "/api/v2/tags",
        headers={"authorization": f"Bearer {service_token}", "x-tenant-id": "tenant_a"},
        HTTP_ACCEPT="text/html",
    )

    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/html")


# --- The dev loop keeps working without collectstatic ---------------------------------


def test_debug_mode_serves_static_from_finders_with_an_empty_static_root(tmp_path):
    # T-E1. With DEBUG=true WhiteNoise switches to its autorefresh/finders path, so a
    # developer never has to run collectstatic. This exercises that path through the
    # middleware (which the test client does run); it does not exercise `runserver`'s
    # own StaticFilesHandler, which sits outside the middleware chain entirely.
    empty_root = tmp_path / "empty-staticfiles"
    empty_root.mkdir()

    with override_settings(DEBUG=True, STATIC_ROOT=str(empty_root)):
        response = Client().get("/static/admin/css/base.css")

    assert response.status_code == 200
    assert not any(empty_root.iterdir()), "served from finders, not from STATIC_ROOT"


# --- A broken manifest must not take the REST API down ---------------------------------


def test_corrupt_manifest_still_leaves_the_app_bootable(tmp_path):
    """The other half of octonomy.W002's ValueError downgrade.

    ``static_root_populated_check`` reports a corrupt manifest as a Warning rather than
    letting it abort ``manage.py check``. That is only correct if the serving path
    survives the same condition — otherwise the check waves a broken deploy through and
    Gunicorn crash-loops a moment later. It does survive: WhiteNoise's startup
    immutability probe catches ValueError from ``staticfiles_storage.url()``. Assert it
    here, against the real middleware, so the two halves cannot drift apart.
    """

    root = tmp_path / "staticfiles"
    (root / "admin" / "css").mkdir(parents=True)
    # The name must LOOK hashed. WhiteNoise's immutable_file_test bails out before calling
    # get_static_url() for an un-hashed name, so an ordinary base.css would never reach the
    # ValueError this test exists to prove is caught.
    (root / "admin" / "css" / "base.0123456789ab.css").write_text("body{}")
    (root / "staticfiles.json").write_text('{"version": "9.9", "paths": {}}')

    with override_settings(STATIC_ROOT=str(root), STORAGES=PRODUCTION_STORAGES):
        # Building the client's handler is what constructs WhiteNoiseMiddleware, which is
        # where an un-caught manifest error would surface.
        response = Client().get("/health/live")

    assert response.status_code == 200


# --- Subpath deployments ---------------------------------------------------------------


@pytest.mark.django_db
def test_subpath_deployment_serves_and_links_static(_manifest_static_root):
    """The app mounted at /octonomy must both LINK and SERVE its assets correctly.

    Two halves, and it is easy to get only one. WhiteNoise matches ``request.path_info``,
    which the WSGI server has already stripped of SCRIPT_NAME, so serving works as long as
    its own prefix is unprefixed. URL *generation* is the other half: ``{% static %}``
    goes through ``settings.STATIC_URL``, and if that does not carry the prefix the
    browser asks the host root for an asset the proxy never routes to this app.

    Both halves need OCTONOMY_STATIC_URL and OCTONOMY_FORCE_SCRIPT_NAME set together;
    dropping either one fails this test.
    """

    with override_settings(
        STATIC_ROOT=str(_manifest_static_root),
        STORAGES=PRODUCTION_STORAGES,
        STATIC_URL="/octonomy/static/",
        FORCE_SCRIPT_NAME="/octonomy",
    ):
        # Half one: the rendered link carries the prefix.
        url = staticfiles_storage.url("admin/css/base.css")
        assert url.startswith("/octonomy/static/")

        # Half two: the app still serves it once the proxy has stripped the prefix. The
        # test client takes PATH_INFO and prepends SCRIPT_NAME itself, so hand it the
        # stripped path — exactly what a real WSGI server passes through.
        response = Client().get(url.removeprefix("/octonomy"), SCRIPT_NAME="/octonomy")

        # FORCE_SCRIPT_NAME must not make the pod's own health endpoints unroutable.
        # Kubernetes probes bypass the ingress and hit the container directly, with no
        # prefix at all; if these stopped answering, every pod would fail its probe and
        # the rollout would revert. Routing matches on path_info, which FORCE_SCRIPT_NAME
        # does not touch — assert it rather than trust it.
        assert Client().get("/health/live").status_code == 200
        assert Client().get("/health/ready").status_code == 200

    assert response.status_code == 200
    assert response.headers["Content-Type"].startswith("text/css")


def test_static_url_is_absolute_so_no_script_prefix_can_be_baked_into_it():
    """Locks the absolute default, which is what makes STATIC_URL deterministic.

    Django script-prefixes a RELATIVE STATIC_URL on first read and caches the result for
    the process (``LazySettings.__getattr__``), so the value would depend on what read it
    first. WhiteNoise's startup index reads it while building the middleware chain, when
    the prefix is still "/" — and even before WhiteNoise it was a race, because the
    Kubernetes health probes reach the pod with no prefix at all. Absolute removes the
    race; a subpath deployment sets OCTONOMY_STATIC_URL explicitly instead.

    Read from the settings MODULE, not from ``django.conf.settings``: the latter has long
    since cached its resolved value by the time any test runs, which would make this pass
    either way.
    """

    from config import settings as settings_module

    assert settings_module.STATIC_URL.startswith("/")


# --- Middleware placement is load-bearing --------------------------------------------


def test_whitenoise_runs_immediately_after_security_middleware():
    from django.conf import settings

    # settings_pytest inherits MIDDLEWARE unchanged from config.settings, so this is
    # the production list. Unconditional by design: gating it on ADMIN_ENABLED would
    # strand the browsable API, which is always on.
    assert settings.MIDDLEWARE[0] == "django.middleware.security.SecurityMiddleware"
    assert settings.MIDDLEWARE[1] == "whitenoise.middleware.WhiteNoiseMiddleware"


def test_production_uses_hashed_manifest_static_storage():
    # The suite runs the plain backend (config/settings_pytest.py, dec-805139c7). Assert
    # the real setting here so that divergence cannot quietly become a downgrade of
    # production too.
    assert (
        PRODUCTION_STORAGES["staticfiles"]["BACKEND"]
        == "whitenoise.storage.CompressedManifestStaticFilesStorage"
    )
    # Both keys are required: Django does not merge STORAGES with global_settings.
    assert set(PRODUCTION_STORAGES) == {"default", "staticfiles"}
