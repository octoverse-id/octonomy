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
        # Named .conf, not .yaml: this is a settings snippet, and a .yaml name would send
        # an intentionally partial fixture through the YAML parser instead.
        ("whitenoise", '"whitenoise.middleware.WhiteNoiseMiddleware",\n'),
        (
            "location-static",
            "    location /static/ {\n        alias /opt/octonomy/staticfiles/;\n    }\n",
        ),
        ("published-image", COMPOSE_WITH_IMAGE),
        ("boot-check", "ExecStartPre=/opt/octonomy/.venv/bin/python manage.py check\n"),
    ],
)
def test_each_marker_is_recognised(run_script, channel_file, label, body):
    result = run_script("check_static_serving.py", channel_file(body, name="channel.conf"))

    assert result.returncode == 0, result.output
    assert label in result.stdout


# --- The two gate-that-matters cases ---------------------------------------------------


def test_a_file_with_no_marker_fails(run_script, channel_file):
    """The gate that matters. A grep matching nothing stays green forever."""

    path = channel_file("services:\n  db:\n    image: postgres:16\n")

    result = run_script("check_static_serving.py", path)

    assert result.returncode == 1
    assert "lost its static story" in result.stdout


def test_a_renamed_or_deleted_file_fails(run_script, tmp_path):
    result = run_script("check_static_serving.py", str(tmp_path / "moved.yaml"))

    assert result.returncode == 1
    assert "no such file" in result.stdout


# --- Mounts that hide the assets baked into the image ----------------------------------
#
# The nastiest drift in this class: the YAML still names the image, still looks entirely
# reasonable, and every /static/* request 404s because the mount hid the files.
#
# These fixtures are complete, VALID documents on purpose. The gate parses the YAML rather
# than matching lines, so a hand-made fragment with impossible indentation would exercise
# the parse-error path instead of the mount logic — and three review rounds of line-oriented
# regex are exactly why it parses now: flow style, `:Z` options, a target that is not the
# last field on its line, and whitespace before a key's colon each slipped past in turn.

SHADOWING = {
    "compose short": """
services:
  api:
    image: {image}:3.1.1
    volumes:
      - .:/app
""",
    "compose short, subdirectory": """
services:
  api:
    image: {image}:3.1.1
    volumes:
      - ./overrides:/app/staticfiles
""",
    "compose short with mode": """
services:
  api:
    image: {image}:3.1.1
    volumes:
      - ./overrides:/app/staticfiles:ro
""",
    "compose short with SELinux relabel": """
services:
  api:
    image: {image}:3.1.1
    volumes:
      - .:/app:Z
""",
    "compose short, quoted": """
services:
  api:
    image: {image}:3.1.1
    volumes:
      - "./overrides:/app/staticfiles:ro"
""",
    "compose long": """
services:
  api:
    image: {image}:3.1.1
    volumes:
      - type: bind
        source: ./overrides
        target: /app
""",
    "compose long, flow style": """
services:
  api:
    image: {image}:3.1.1
    volumes: [{{type: bind, source: ., target: /app}}]
""",
    "compose long, flow style, target not last": """
services:
  api:
    image: {image}:3.1.1
    volumes: [{{type: bind, target: /app, source: .}}]
""",
    "compose short, flow list alongside another mount": """
services:
  api:
    image: {image}:3.1.1
    volumes: [".:/app", "./o:/srv/other"]
""",
    "compose long, space before the key colon": """
services:
  api:
    image: {image}:3.1.1
    volumes:
      - type : bind
        target : /app
""",
    "kubernetes mountPath": """
spec:
  containers:
    - image: {image}:3.1.1
      volumeMounts:
        - name: overrides
          mountPath: /app
""",
    "kubernetes mountPath, quoted": """
spec:
  containers:
    - image: {image}:3.1.1
      volumeMounts:
        - name: overrides
          mountPath: "/app"
""",
    "compose long, quoted target with surrounding whitespace": """
services:
  api:
    image: {image}:3.1.1
    volumes:
      - type: bind
        target: "  /app/staticfiles  "
""",
    "kubernetes mountPath, trailing slash": """
spec:
  containers:
    - image: {image}:3.1.1
      volumeMounts:
        - name: overrides
          mountPath: /app/
""",
    "kubernetes mountPath, below the collected assets": """
spec:
  containers:
    - image: {image}:3.1.1
      volumeMounts:
        - name: overrides
          mountPath: /app/staticfiles/admin
""",
    # A tmpfs over the collected tree is the quietest break of all: the container starts
    # normally and every asset 404s off an empty in-memory filesystem.
    "compose tmpfs list": """
services:
  api:
    image: {image}:3.1.1
    tmpfs:
      - /app/staticfiles
""",
    "compose tmpfs scalar": """
services:
  api:
    image: {image}:3.1.1
    tmpfs: /app
""",
    # `- /app` with no source is an ANONYMOUS VOLUME mounted at /app. Easy to overlook,
    # and it hides the tree exactly like a bind mount.
    "compose anonymous volume": """
services:
  api:
    image: {image}:3.1.1
    volumes:
      - /app
""",
    # Compose normalises the path, so this really does land on /app.
    "compose long, path needing normalisation": """
services:
  api:
    image: {image}:3.1.1
    volumes:
      - type: bind
        source: .
        target: /srv/../app
""",
    # Compose substitutes the default when the variable is unset, which is the normal state
    # of a committed example file — so the default is what a reader actually deploys.
    "compose long, interpolated with a default": """
services:
  api:
    image: {image}:3.1.1
    volumes:
      - type: bind
        source: .
        target: ${{OCTONOMY_TARGET:-/app}}
""",
    # The first colon belongs to the drive letter, not the source/target separator.
    "compose short, windows drive letter source": """
services:
  api:
    image: {image}:3.1.1
    volumes:
      - 'C:\\work:/app'
""",
    "kubernetes mountPath, flow style with a sibling field": """
spec:
  containers:
    - image: {image}:3.1.1
      volumeMounts: [{{mountPath: /app, readOnly: true}}]
""",
}

UNRELATED = {
    "named volume elsewhere": """
services:
  api:
    image: {image}:3.1.1
    volumes:
      - octonomy_pgdata:/var/lib/postgresql/data
""",
    "tmp mount": """
spec:
  containers:
    - image: {image}:3.1.1
      volumeMounts:
        - name: tmp
          mountPath: /tmp
""",
    # /apple is not /app. A prefix comparison would be wrong here.
    "a path that merely starts with app": """
spec:
  containers:
    - image: {image}:3.1.1
      volumeMounts:
        - name: x
          mountPath: /apple/data
""",
    "app deeper in the tree": """
spec:
  containers:
    - image: {image}:3.1.1
      volumeMounts:
        - name: x
          mountPath: /srv/app
""",
    "flow style over an unrelated path": """
services:
  api:
    image: {image}:3.1.1
    volumes: [{{target: /apple}}]
""",
    # Confirmed with `docker compose config` as literal absolute targets, not mounts over
    # /app. The line-oriented matcher reported all three as shadowing; parsing does not.
    "literal target with a trailing comma": """
services:
  api:
    image: {image}:3.1.1
    volumes:
      - type: bind
        target: "/app,"
""",
    "literal target with a trailing brace": """
services:
  api:
    image: {image}:3.1.1
    volumes:
      - type: bind
        target: "/app}}"
""",
    "a database url whose path is /app": """
services:
  api:
    image: {image}:3.1.1
    environment:
      DATABASE_URL: postgres://u:p@h:5432/app
""",
    # mountPath is scoped through volumeMounts for the same reason target is scoped through
    # volumes: both are ordinary annotation and label keys elsewhere.
    "the word mountPath in an annotation": """
metadata:
  annotations:
    mountPath: /app
  labels:
    app: octonomy
spec:
  containers:
    - image: {image}:3.1.1
""",
    "a tmpfs somewhere harmless": """
services:
  api:
    image: {image}:3.1.1
    tmpfs:
      - /run
""",
    "the word target outside a volumes block": """
services:
  api:
    image: {image}:3.1.1
    labels:
      target: /app
""",
}


@pytest.mark.parametrize("label", sorted(SHADOWING))
def test_a_mount_over_the_baked_assets_fails(run_script, channel_file, label):
    path = channel_file(SHADOWING[label].format(image=IMAGE))

    result = run_script("check_static_serving.py", path)

    assert result.returncode == 1, result.output
    assert "hides the static assets" in result.stdout


@pytest.mark.parametrize("label", sorted(UNRELATED))
def test_unrelated_mounts_are_left_alone(run_script, channel_file, label):
    path = channel_file(UNRELATED[label].format(image=IMAGE))

    assert run_script("check_static_serving.py", path).returncode == 0, label


def test_an_unparseable_manifest_fails(run_script, channel_file):
    """A manifest that does not parse cannot be deployed either, so its mounts being
    uncheckable is a real failure rather than a reason to skip quietly."""

    path = channel_file(f"services:\n  api:\n    image: {IMAGE}:3.1.1\n   bad: indentation\n")

    result = run_script("check_static_serving.py", path)

    assert result.returncode == 1
    assert "not parseable as YAML" in result.stdout


def test_a_non_yaml_channel_file_is_not_mount_checked(run_script, channel_file):
    """A Dockerfile, systemd unit or nginx conf cannot declare a container mount, so text
    that merely looks like one must not fail them."""

    path = channel_file(
        "RUN python manage.py collectstatic --noinput\nVOLUME /app\nWORKDIR /app\n",
        name="Dockerfile",
    )

    assert run_script("check_static_serving.py", path).returncode == 0


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
        # systemd treats a leading ';' as a comment exactly like '#', and it is one of the
        # five formats this gate scans — so a unit that runs no check at all satisfied the
        # boot-check marker off a disabled line.
        "[Service]\n; ExecStartPre=/opt/octonomy/.venv/bin/python manage.py check\n"
        "ExecStart=/bin/true\n",
        "[Service]\n;ExecStartPre=/opt/octonomy/.venv/bin/python manage.py check\n",
    ],
)
def test_a_marker_that_only_appears_in_a_comment_does_not_count(run_script, channel_file, body):
    result = run_script("check_static_serving.py", channel_file(body))

    assert result.returncode == 1
    assert "outside comments" in result.stdout


def test_a_commented_out_mount_is_not_treated_as_shadowing(run_script, channel_file):
    """The converse: a disabled mount must not fail the gate, or it becomes noise."""

    path = channel_file(COMPOSE_WITH_IMAGE + "    volumes:\n      # - .:/app\n")

    assert run_script("check_static_serving.py", path).returncode == 0


def test_a_shadowing_mount_with_a_trailing_comment_still_fails(run_script, channel_file):
    path = channel_file(COMPOSE_WITH_IMAGE + "    volumes:\n      - .:/app  # dev override\n")

    result = run_script("check_static_serving.py", path)

    assert result.returncode == 1
    assert "hides the static assets" in result.stdout


# --- Reporting behaviour ---------------------------------------------------------------


def test_every_file_is_checked_not_just_the_first(run_script, channel_file):
    good = channel_file(COMPOSE_WITH_IMAGE, name="compose.yaml")
    bad = channel_file("apiVersion: apps/v1\n", name="deployment.yaml")

    result = run_script("check_static_serving.py", good, bad)

    assert result.returncode == 1
    assert "deployment.yaml" in result.stdout


def test_all_problems_are_reported_not_just_the_first(run_script, channel_file):
    first = channel_file("apiVersion: apps/v1\n", name="a.yaml")
    second = channel_file("kind: Deployment\n", name="b.yaml")

    result = run_script("check_static_serving.py", first, second)

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

    assert run_script("check_static_serving.py", path).returncode == 0


def test_usage_error_without_arguments(run_script):
    assert run_script("check_static_serving.py").returncode == 2


# --- The real tree ----------------------------------------------------------------------


def test_the_real_deploy_channels_pass(run_script, scripts_dir):
    """Runs the gate over the files the Makefile actually passes it.

    Without this the suite could stay green while the shipped tree drifted — the tests
    above only ever exercise fixtures.
    """

    repo = scripts_dir.parent
    result = run_script(
        "check_static_serving.py",
        str(repo / "Dockerfile"),
        str(repo / "deploy/docker/compose.yaml"),
        str(repo / "deploy/kubernetes/deployment.yaml"),
        str(repo / "deploy/systemd/octonomy.service"),
        str(repo / "deploy/systemd/nginx-octonomy.conf"),
    )

    assert result.returncode == 0, result.output


def test_a_mount_target_that_cannot_be_resolved_is_reported(run_script, channel_file):
    """A bare ``${VAR}`` has no knowable value here, so the gate says it could not check
    rather than reporting success. Fails closed only where the destination is genuinely
    undeterminable — an interpolation WITH a default resolves and is judged normally."""

    path = channel_file(
        f"services:\n  api:\n    image: {IMAGE}:3.1.1\n"
        "    volumes:\n      - type: bind\n        target: ${OCTONOMY_TARGET}\n"
    )

    result = run_script("check_static_serving.py", path)

    assert result.returncode == 1
    assert "cannot tell whether it lands on the baked assets" in result.stdout


def test_an_upper_case_yaml_extension_is_still_mount_checked(run_script, channel_file):
    """`Path("compose.YAML").suffix` is `.YAML`. A case-sensitive comparison would
    marker-check a renamed manifest while silently skipping its mounts."""

    path = channel_file(
        f"services:\n  api:\n    image: {IMAGE}:3.1.1\n    volumes:\n      - .:/app\n",
        name="compose.YAML",
    )

    result = run_script("check_static_serving.py", path)

    assert result.returncode == 1
    assert "hides the static assets" in result.stdout
