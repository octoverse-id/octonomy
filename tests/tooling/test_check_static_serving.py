"""The deploy/ static-serving drift gate (#144, part of epic #142).

Mirrors tests/tooling/test_check_image_refs.py, including its two gate-that-matters
cases: a file with no reference must fail, and a renamed or deleted file must fail. A
guard that can only pass is worse than no guard, because it stops anyone checking by hand
— which is precisely how #142 survived a release.
"""

from __future__ import annotations

import pytest

IMAGE = "ghcr.io/octoverse-id/octonomy"

COMPOSE_WITH_IMAGE = f"services:\n  api:\n    image: {IMAGE}:3.1.1\n"


@pytest.fixture
def channel_file(tmp_path):
    """Write a fragment that looks like one of the files the real gate scans."""

    def _write(body: str, name: str = "compose.yaml") -> str:
        path = tmp_path / name
        path.write_text(body)
        return str(path)

    return _write


# --- Each recognised marker is accepted on its own ------------------------------------


@pytest.mark.parametrize(
    ("label", "body"),
    [
        ("collectstatic", "RUN python manage.py collectstatic --noinput\n"),
        ("whitenoise", '    "whitenoise.middleware.WhiteNoiseMiddleware",\n'),
        (
            "location-static",
            "    location /static/ {\n        alias /opt/octonomy/staticfiles/;\n    }\n",
        ),
        ("published-image", COMPOSE_WITH_IMAGE),
        ("boot-check", "ExecStartPre=/opt/octonomy/.venv/bin/python manage.py check\n"),
    ],
)
def test_each_marker_is_recognised(run_script, channel_file, label, body):
    result = run_script("check-static-serving.sh", channel_file(body))

    assert result.returncode == 0, result.output
    assert label in result.stdout


# --- The two gate-that-matters cases ---------------------------------------------------


def test_a_file_with_no_marker_fails(run_script, channel_file):
    """The gate that matters. A grep matching nothing stays green forever."""

    path = channel_file("services:\n  db:\n    image: postgres:16\n")

    result = run_script("check-static-serving.sh", path)

    assert result.returncode == 1
    assert "lost its static story" in result.stdout


def test_a_renamed_or_deleted_file_fails(run_script, tmp_path):
    result = run_script("check-static-serving.sh", str(tmp_path / "moved.yaml"))

    assert result.returncode == 1
    assert "no such file" in result.stdout


# --- Mounts that hide the assets baked into the image ----------------------------------
#
# The nastiest drift in this class: the YAML still names the image, still looks entirely
# reasonable, and every /static/* request 404s because the mount hid the files.


@pytest.mark.parametrize(
    "mount",
    [
        # Compose short syntax, bare and quoted, with and without a mode suffix.
        "    volumes:\n      - .:/app\n",
        "    volumes:\n      - ./overrides:/app/staticfiles\n",
        "    volumes:\n      - ./overrides:/app/staticfiles:ro\n",
        '    volumes:\n      - "./overrides:/app/staticfiles:ro"\n',
        "    volumes:\n      - './overrides:/app'\n",
        # Compose long syntax.
        "    volumes:\n      - type: bind\n        source: ./o\n        target: /app\n",
        "    volumes:\n      - type: bind\n        source: ./o\n        target: /app/staticfiles\n",
        # Kubernetes, bare and quoted.
        "      volumeMounts:\n        - name: x\n          mountPath: /app\n",
        "      volumeMounts:\n        - name: x\n          mountPath: /app/staticfiles\n",
        '      volumeMounts:\n        - name: x\n          mountPath: "/app"\n',
        # Trailing slash, and a path BELOW the collected assets — that subtree is hidden
        # just as effectively as the whole directory.
        "      volumeMounts:\n        - name: x\n          mountPath: /app/\n",
        "      volumeMounts:\n        - name: x\n          mountPath: /app/staticfiles/admin\n",
    ],
)
def test_a_mount_over_the_baked_assets_fails(run_script, channel_file, mount):
    path = channel_file(COMPOSE_WITH_IMAGE + mount)

    result = run_script("check-static-serving.sh", path)

    assert result.returncode == 1
    assert "hides the static assets" in result.stdout


@pytest.mark.parametrize(
    "mount",
    [
        # Unrelated mounts must not trip it, or the gate becomes noise and gets deleted.
        "    volumes:\n      - octonomy_pgdata:/var/lib/postgresql/data\n",
        "      volumeMounts:\n        - name: tmp\n          mountPath: /tmp\n",
        # /apple is not /app. A prefix match would be wrong here.
        "      volumeMounts:\n        - name: x\n          mountPath: /apple\n",
        '      volumeMounts:\n        - name: x\n          mountPath: "/apple/data"\n',
        # A path that merely mentions /app deeper in the tree is not a mount over it.
        "      volumeMounts:\n        - name: x\n          mountPath: /srv/app\n",
        # A bare `/app` line is not a mount. It matters because the matcher runs over
        # grep -n output, so this arrives as `4:/app` and would satisfy a naive `:/app`.
        "/app\n",
    ],
)
def test_unrelated_mounts_are_left_alone(run_script, channel_file, mount):
    path = channel_file(COMPOSE_WITH_IMAGE + mount)

    assert run_script("check-static-serving.sh", path).returncode == 0


# --- Comments do not count as a static story -------------------------------------------
#
# The sharpest "passes while broken" shape available to this gate, and it was real before
# the fix: the Dockerfile's comment block explains collectstatic at length, so deleting the
# actual `RUN ... collectstatic` line left the gate matching the prose and reporting ok.


@pytest.mark.parametrize(
    "body",
    [
        # Trailing (inline) comments, not just whole-line ones. This is the shape the PR
        # bot caught: the marker greps used to run before inline comments were stripped.
        "services:\n  api:\n    build: .\n    command: echo ok # collectstatic used to run here\n",
        "FROM python:3.14-slim\nRUN true  # whitenoise serves these\n",
        "# this stage used to run collectstatic\nFROM python:3.14-slim\n",
        "# image: ghcr.io/octoverse-id/octonomy:3.1.1\nservices:\n  api:\n    build: .\n",
        "  # WhiteNoiseMiddleware used to be here\nMIDDLEWARE = []\n",
        "    # location /static/ was removed\n    location / {\n    }\n",
    ],
)
def test_a_marker_that_only_appears_in_a_comment_does_not_count(run_script, channel_file, body):
    result = run_script("check-static-serving.sh", channel_file(body))

    assert result.returncode == 1
    assert "outside comments" in result.stdout


def test_a_commented_out_mount_is_not_treated_as_shadowing(run_script, channel_file):
    """The converse: a disabled mount must not fail the gate, or it becomes noise."""

    path = channel_file(COMPOSE_WITH_IMAGE + "    volumes:\n      # - .:/app\n")

    assert run_script("check-static-serving.sh", path).returncode == 0


def test_a_shadowing_mount_with_a_trailing_comment_still_fails(run_script, channel_file):
    path = channel_file(COMPOSE_WITH_IMAGE + "    volumes:\n      - .:/app  # dev override\n")

    result = run_script("check-static-serving.sh", path)

    assert result.returncode == 1
    assert "hides the static assets" in result.stdout


def test_reported_line_numbers_match_the_file(run_script, channel_file):
    """Comments are stripped before matching, so the numbers must survive that."""

    path = channel_file("# one\n# two\n" + COMPOSE_WITH_IMAGE + "    volumes:\n      - .:/app\n")

    result = run_script("check-static-serving.sh", path)

    assert result.returncode == 1
    # `- .:/app` is the 7th line of the file as written.
    assert "7:" in result.stdout


# --- Reporting behaviour ---------------------------------------------------------------


def test_every_file_is_checked_not_just_the_first(run_script, channel_file):
    good = channel_file(COMPOSE_WITH_IMAGE, name="compose.yaml")
    bad = channel_file("apiVersion: apps/v1\n", name="deployment.yaml")

    result = run_script("check-static-serving.sh", good, bad)

    assert result.returncode == 1
    assert "deployment.yaml" in result.stdout


def test_all_problems_are_reported_not_just_the_first(run_script, channel_file):
    first = channel_file("apiVersion: apps/v1\n", name="a.yaml")
    second = channel_file("kind: Deployment\n", name="b.yaml")

    result = run_script("check-static-serving.sh", first, second)

    assert result.returncode == 1
    assert "a.yaml" in result.stdout
    assert "b.yaml" in result.stdout
    assert "2 problem(s)" in result.stderr


def test_counts_are_not_asserted_only_presence(run_script, channel_file):
    """Adding a service adds markers and must not break the gate."""

    path = channel_file(
        f"  api:\n    image: {IMAGE}:3.1.1\n"
        f"  migrate:\n    image: {IMAGE}:3.1.1\n"
        f"  dispatcher:\n    image: {IMAGE}:3.1.1\n"
    )

    assert run_script("check-static-serving.sh", path).returncode == 0


def test_usage_error_without_arguments(run_script):
    assert run_script("check-static-serving.sh").returncode == 2


# --- The real tree ----------------------------------------------------------------------


def test_the_real_deploy_channels_pass(run_script, scripts_dir):
    """Runs the gate over the files the Makefile actually passes it.

    Without this the suite could stay green while the shipped tree drifted — the tests
    above only ever exercise fixtures.
    """

    repo = scripts_dir.parent
    result = run_script(
        "check-static-serving.sh",
        str(repo / "Dockerfile"),
        str(repo / "deploy/docker/compose.yaml"),
        str(repo / "deploy/kubernetes/deployment.yaml"),
        str(repo / "deploy/systemd/octonomy.service"),
        str(repo / "deploy/systemd/nginx-octonomy.conf"),
    )

    assert result.returncode == 0, result.output
