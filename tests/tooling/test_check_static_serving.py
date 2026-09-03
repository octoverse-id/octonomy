"""The deploy/ static-serving drift gate (#144, part of epic #142).

Mirrors tests/tooling/test_check_image_refs.py, including its two gate-that-matters
cases: a file with no reference must fail, and a renamed or deleted file must fail. A
guard that can only pass is worse than no guard, because it stops anyone checking by hand
— which is precisely how #142 survived a release.
"""

from __future__ import annotations

import re

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
    # Interpolation must be expanded BEFORE the entry is split on colons: splitting first
    # tears ${TARGET:-/app} at the operator's own colon, leaving a `${TARGET` fragment that
    # no "still interpolated?" test recognises, and a bind mount onto /app reads as an
    # ordinary path.
    "compose short, interpolated target": """
services:
  api:
    image: {image}:3.1.1
    volumes:
      - './src:${{TARGET:-/app}}'
""",
    "compose short, interpolated source": """
services:
  api:
    image: {image}:3.1.1
    volumes:
      - '${{SOURCE:-./src}}:/app'
""",
    # Everything after the first colon of a tmpfs entry is mount options.
    "compose tmpfs with options": """
services:
  api:
    image: {image}:3.1.1
    tmpfs:
      - /app:mode=755,size=64m
""",
    # A config or secret mounted over a file REPLACES it — this one swaps the production
    # manifest out from under WhiteNoise before Django starts.
    "compose config over the staticfiles manifest": """
services:
  api:
    image: {image}:3.1.1
    configs:
      - source: broken_manifest
        target: /app/staticfiles/staticfiles.json
configs:
  broken_manifest:
    content: '{{}}'
""",
    "compose secret inside the collected tree": """
services:
  api:
    image: {image}:3.1.1
    secrets:
      - source: creds
        target: /app/staticfiles/creds
""",
    # An ancestor mount covers the tree just as thoroughly.
    "a mount over the container root": """
services:
  api:
    image: {image}:3.1.1
    volumes:
      - .:/
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
    # A sibling under /app hides nothing: the collected tree is /app/staticfiles.
    "a log directory beside the collected assets": """
services:
  api:
    image: {image}:3.1.1
    volumes:
      - ./logs:/app/logs
""",
    "a similarly named sibling directory": """
services:
  api:
    image: {image}:3.1.1
    volumes:
      - ./old:/app/staticfiles-old
""",
    # `target` here is an opaque volume-driver option, not a destination. Latching the
    # collection scope through every descendant read it as a mount.
    "a top-level volume driver option named target": """
services:
  api:
    image: {image}:3.1.1
    volumes:
      - data:/srv/data
volumes:
  data:
    driver: local
    driver_opts:
      target: /app
""",
    # The Kubernetes equivalent of the same shape.
    "a csi volumeAttributes entry named target": """
spec:
  containers:
    - image: {image}:3.1.1
  volumes:
    - csi:
        volumeAttributes:
          target: /app
""",
    "a config mounted somewhere unrelated": """
services:
  api:
    image: {image}:3.1.1
    configs:
      - source: cfg
        target: /etc/octonomy.json
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


@pytest.mark.parametrize(
    ("label", "directive", "accepted"),
    [
        ("bare upstream", "proxy_pass http://octonomy", True),
        ("trailing slash preserves the path", "proxy_pass http://octonomy/", True),
        ("explicit port", "proxy_pass http://octonomy:8000", True),
        ("port and slash", "proxy_pass http://octonomy:8000/", True),
        ("https", "proxy_pass https://octonomy", True),
        # A URI REPLACES the part `location /` matched, so /static/app.css arrives upstream
        # as /api/static/app.css and never reaches WhiteNoise's prefix.
        ("a base URI rewrites the path", "proxy_pass http://octonomy/api/", False),
        ("a base URI without a trailing slash", "proxy_pass http://octonomy/api", False),
        ("a different host", "proxy_pass http://octonomy.example", False),
        ("a different upstream", "proxy_pass http://octonomy-old", False),
    ],
)
def test_only_a_path_preserving_proxy_pass_counts(label, directive, accepted):
    assert bool(NGINX_PROXY_TO_APP.match(directive)) is accepted, label


def test_the_real_deploy_channels_pass(run_script, scripts_dir):
    """Runs the gate over the files the Makefile actually passes it.

    Without this the suite could stay green while the shipped tree drifted — the tests
      above only ever exercise fixtures. Keep this list in step with the Makefile's
    `static-check` target. The two systemd files are deliberately absent from both — see the
    gate's docstring, and the nginx and runbook tests below that cover that channel properly.
    """

    repo = scripts_dir.parent
    result = run_script(
        "check_static_serving.py",
        str(repo / "Dockerfile"),
        str(repo / "deploy/docker/compose.yaml"),
        str(repo / "deploy/kubernetes/deployment.yaml"),
    )

    assert result.returncode == 0, result.output


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


@pytest.mark.parametrize(
    ("label", "entry"),
    [
        # Only the default-value forms can be resolved here. These three depend on the
        # deploying environment, so they must be reported rather than quietly passed.
        ("bare variable", "target: ${OCTONOMY_TARGET}"),
        ("error form", "- './src:${OCTONOMY_TARGET:?must be set}'"),
        ("alternate form", "- './src:${OCTONOMY_TARGET:+/app}'"),
    ],
)
def test_a_mount_target_that_cannot_be_resolved_is_reported(run_script, channel_file, label, entry):
    body = f"services:\n  api:\n    image: {IMAGE}:3.1.1\n    volumes:\n"
    body += (
        f"      - type: bind\n        {entry}\n" if entry.startswith("target") else f"    {entry}\n"
    )

    result = run_script("check_static_serving.py", channel_file(body))

    assert result.returncode == 1, f"{label}: {result.output}"
    assert "cannot tell whether it lands on the baked assets" in result.stdout


# --- The nginx template's static invariants --------------------------------------------
#
# Asserted here rather than through `make static-check`'s marker gate, because both are
# questions about operative directives and block structure, not token presence. A first cut
# of these tests still had bypasses — a brace counter is not by itself an improvement over a
# grep — so both predicates below are written against nginx's actual grammar and each named
# bypass is mutation-tested.

NGINX_UPSTREAM = "octonomy"

# Every nginx spelling that claims the static prefix. Searched anywhere in the line, not
# anchored to its start: `server { listen 443 ssl; location /static/ { ... } }` is valid, and
# anchoring missed it while the catch-all predicate below deliberately supports one-liners.
# The trailing boundary is what keeps `location /staticfiles-report` out.
_STATIC_LOCATION = r"""
    location\s+
    (?:(?:=|\^~|~\*?)\s*)?
    ["']?
    \^?/static(?:/|["'\s{]|$)
"""
NGINX_STATIC_LOCATION = re.compile(_STATIC_LOCATION, re.VERBOSE)

# `~*` is nginx's CASE-INSENSITIVE regex location, so `location ~* ^/STATIC/` claims the same
# prefix. Only that modifier gets case-insensitive treatment: a bare `location /Static/` is a
# genuinely different prefix in nginx, and flagging it would be a false alarm.
NGINX_STATIC_LOCATION_CI = re.compile(
    r"""location\s+~\*\s*["']?\^?/static(?:/|["'\s{]|$)""", re.VERBOSE | re.IGNORECASE
)

# The upstream must end the directive — optional port, and at most the bare trailing slash
# that preserves the request path. Two things this rules out, both silently fatal to static:
#
#   proxy_pass http://octonomy.example    a bare `\b` treats the dot as a boundary, so a
#   proxy_pass http://octonomy-old        completely different host reads as the upstream
#   proxy_pass http://octonomy/api/       a URI REPLACES the part `location /` matched, so
#                                         /static/app.css goes upstream as /api/static/app.css
#                                         and never reaches WhiteNoise's /static/ prefix
NGINX_PROXY_TO_APP = re.compile(rf"^proxy_pass\s+https?://{NGINX_UPSTREAM}(?::\d+)?/?$")


def claims_static(line):
    """True when ``line`` contains a location that would answer /static/ requests."""

    return bool(NGINX_STATIC_LOCATION.search(line) or NGINX_STATIC_LOCATION_CI.search(line))


def operative_lines(text):
    """``text`` with comments stripped — trailing ones too, not just whole lines.

    Shared by the nginx and runbook readers below, because both were bitten by the same
    thing: a directive or a command left only in prose satisfying a search for it. Trailing
    comments matter as much as whole-line ones — `try_files ...; # proxy_pass http://octonomy;`
    and `sudo ...  # collectstatic` both read as operative to a naive filter.
    """

    return [re.sub(r"#.*$", "", line) for line in text.splitlines()]


def _nginx_conf(scripts_dir):
    """The shipped template's operative lines."""

    path = scripts_dir.parent / "deploy/systemd/nginx-octonomy.conf"
    return operative_lines(path.read_text())


def _catch_all_proxies_to_app(lines):
    """True when a `location / { ... }` block proxies to the app at its OWN depth.

    Tokenised on braces rather than read line by line, because nginx does not care where
    newlines fall. Both of these are one physical line, and a line-oriented reader gets one
    of them wrong whichever way it is written:

        location / { proxy_pass http://octonomy; }                      -> must PASS
        location / { if ($uri ~ ^/api/) { proxy_pass http://octonomy; } } -> must FAIL

    The second is the nested-conditional bypass again: /static/ has no proxy handler, so
    depth has to be tracked through braces that appear mid-line, not just at line ends.

    Known limit: this reads one file. An operative `include` pulling a location in from a
    snippet would be invisible to it. The shipped template has none — its only `include`
    mentions are in comments — and if one is ever added, this predicate needs to follow it.
    """

    depth = 0
    catch_all_depth = None
    header = ""

    for token in re.split(r"([{}])", "\n".join(lines)):
        if token == "{":
            depth += 1
            # A block header ending in `location /` opens the catch-all. `location = /` does
            # not: an exact match answers the root path alone, never /static/anything.
            if catch_all_depth is None and re.search(r"location\s+/\s*$", header.strip()):
                catch_all_depth = depth
            header = ""
        elif token == "}":
            if catch_all_depth is not None and depth == catch_all_depth:
                catch_all_depth = None
            depth -= 1
            header = ""
        else:
            if catch_all_depth is not None and depth == catch_all_depth:
                for directive in token.split(";"):
                    if NGINX_PROXY_TO_APP.match(directive.strip()):
                        return True
            header = token

    return False


def test_the_shipped_nginx_config_has_no_static_location(scripts_dir):
    """#145 removed the alias deliberately, so it must not quietly grow back — in any of
    nginx's location spellings, not just the bare one.

    A single `expires` value cannot serve both filename kinds: `7d` under-caches the hashed
    assets that could be cached forever, and `max`/`immutable` pins the UNHASHED paths in
    every browser cache indefinitely, so the next upgrade never reaches those clients. If a
    future change does need the alias back, it has to take over the caching contract
    deliberately — and update this test to say so.
    """

    offenders = [line for line in _nginx_conf(scripts_dir) if claims_static(line)]

    assert not offenders, (
        f"nginx has an operative location claiming /static/ again ({offenders}); it now owns "
        "the caching contract for those responses"
    )


def test_the_shipped_nginx_config_proxies_everything_to_the_app(scripts_dir):
    """With no static location, the catch-all is what carries /static/ to WhiteNoise.

    Narrowing it to `location /api/`, or burying the proxy_pass inside a conditional, would
    take static off this channel while every token-level check stayed green.
    """

    assert _catch_all_proxies_to_app(_nginx_conf(scripts_dir)), (
        f"no `location / {{ proxy_pass http://{NGINX_UPSTREAM}; }}` at the block's own depth: "
        "/static/ no longer reaches the app, so this channel has lost static serving"
    )


@pytest.mark.parametrize(
    "form",
    [
        "    location /static/ {",
        "    location /static {",
        '    location "/static/" {',
        "    location '/static/' {",
        "    location ^~ /static/ {",
        "    location ~* ^/static/ {",
        "    location = /static/x {",
        "    location ~ /static/.*\\.css$ {",
        # One physical line — an anchored search missed it while the catch-all predicate
        # deliberately supports one-liners.
        "server { listen 443 ssl; location /static/ { alias /opt/octonomy/staticfiles/; } }",
        # `~*` is nginx's case-INSENSITIVE regex location, so these claim the same prefix.
        "    location ~* ^/STATIC/ {",
        "    location ~* ^/Static/ {",
    ],
)
def test_every_nginx_static_location_form_is_recognised(form):
    assert claims_static(form), form


@pytest.mark.parametrize(
    "form",
    [
        "    location / {",
        "    location /api/ {",
        "    location /staticfiles-report {",
        "    location = /health/live {",
        # A bare prefix is case-SENSITIVE in nginx, so this is a genuinely different
        # location and flagging it would be a false alarm.
        "    location /Static/ {",
        # No `location` keyword: mentioning the path is not claiming it.
        "        proxy_pass http://backend/static/;",
    ],
)
def test_unrelated_nginx_locations_are_not_flagged(form):
    assert not claims_static(form), form


@pytest.mark.parametrize(
    ("label", "conf"),
    [
        # The proxy_pass is inside the catch-all but nested in a conditional, so /static/
        # is not proxied.
        (
            "nested in an /api/ conditional",
            "    location / {\n        if ($uri ~ ^/api/) {\n"
            "            proxy_pass http://octonomy;\n        }\n    }\n",
        ),
        # Present only as a comment.
        ("commented out", "    location / {\n        # proxy_pass http://octonomy;\n    }\n"),
        # Points somewhere other than the app.
        ("wrong upstream", "    location / {\n        proxy_pass http://elsewhere;\n    }\n"),
        # No catch-all at all.
        (
            "api-only catch-all",
            "    location /api/ {\n        proxy_pass http://octonomy;\n    }\n",
        ),
        (
            "health-only",
            "    location = /health/live {\n        proxy_pass http://octonomy;\n    }\n",
        ),
        # The nested-conditional bypass again, on ONE line — braces have to be tracked
        # mid-line, not only at line ends.
        (
            "one-line nested conditional",
            "location / { if ($uri ~ ^/api/) { proxy_pass http://octonomy; } }\n",
        ),
        # `\b` treats the dot as a word boundary, so a different host read as the upstream.
        (
            "a different host sharing the name's prefix",
            "location / { proxy_pass http://octonomy.example; }\n",
        ),
        (
            "a different upstream sharing the name's prefix",
            "location / { proxy_pass http://octonomy-old; }\n",
        ),
    ],
)
def test_a_catch_all_that_does_not_proxy_static_is_rejected(label, conf):
    lines = [re.sub(r"#.*$", "", line) for line in conf.splitlines()]

    assert not _catch_all_proxies_to_app(lines), label


@pytest.mark.parametrize(
    ("label", "conf"),
    [
        ("multi-line", "    location / {\n        proxy_pass http://octonomy;\n    }\n"),
        # Valid nginx, and a first cut of this predicate reported it as a failure.
        ("single line", "    location / { proxy_pass http://octonomy; }\n"),
        ("https upstream", "    location / {\n        proxy_pass https://octonomy;\n    }\n"),
        ("upstream with a port", "    location / { proxy_pass http://octonomy:8000; }\n"),
        ("upstream with a trailing slash", "    location / { proxy_pass http://octonomy/; }\n"),
        (
            "nested inside a one-line server block",
            "server { listen 443; location / { proxy_pass http://octonomy; } }\n",
        ),
    ],
)
def test_valid_catch_all_forms_are_accepted(label, conf):
    lines = [re.sub(r"#.*$", "", line) for line in conf.splitlines()]

    assert _catch_all_proxies_to_app(lines), label


@pytest.mark.parametrize(
    ("label", "text", "expected"),
    [
        ("whole-line comment", "# proxy_pass http://octonomy;", ""),
        ("trailing comment", "location / { # proxy_pass http://octonomy;", "location / { "),
        ("indented whole-line", "    #  collectstatic", "    "),
        ("no comment", "proxy_pass http://octonomy;", "proxy_pass http://octonomy;"),
    ],
)
def test_operative_lines_strips_comments_in_both_positions(label, text, expected):
    assert operative_lines(text) == [expected], label


def test_a_catch_all_whose_proxy_pass_is_only_an_inline_comment_is_rejected():
    """The shape a whole-line-comment filter would let through."""

    conf = "    location / {\n        try_files $uri @nope;  # proxy_pass http://octonomy;\n    }\n"

    assert not _catch_all_proxies_to_app(operative_lines(conf))


def test_a_static_location_in_a_trailing_comment_is_not_flagged():
    conf = "    listen 443 ssl;  # location /static/ { alias ...; } was here once\n"

    assert not any(claims_static(line) for line in operative_lines(conf))


def test_a_real_catch_all_is_accepted():
    conf = (
        "    location / {\n        proxy_pass http://octonomy;\n"
        "        proxy_read_timeout 30s;\n    }\n"
    )

    assert _catch_all_proxies_to_app([re.sub(r"#.*$", "", line) for line in conf.splitlines()])


# --- The VPS runbook is the systemd channel's static declaration ------------------------
#
# `make static-check` no longer scans octonomy.service: a systemd unit says nothing about
# static, and the `manage.py check` line it used to match is not a static signal either.
# (Since #146 octonomy.W002 does fire on an uncollected root whether or not the admin is
# on — but that is a runtime warning about THIS host, not a declaration by the unit file,
# and it cannot see the stale root an upgrade leaves behind.) What actually carries this
# channel is the runbook telling the operator to collect, on install AND on upgrade.
# Deleting either step was the #145 defect; these assert it cannot come back.

DEPLOYMENT_DOC = "docs/deployment.md"


def _option_c(scripts_dir):
    """Option C's install and upgrade text, comment lines removed.

    Comments are dropped for the same reason the drift gate drops them: the upgrade block
    explains `collectstatic` in prose, so a substring search would be satisfied by the
    explanation after someone deleted the command it describes.
    """

    text = (scripts_dir.parent / DEPLOYMENT_DOC).read_text()
    section = text[text.index("## Option C") :]
    section = section[: section.index("\n## ")]
    install, _, upgrade = section.partition("**Upgrades:**")

    return "\n".join(operative_lines(install)), "\n".join(operative_lines(upgrade))


# The commands themselves, not the words. `echo collectstatic` satisfied a substring search;
# so did leaving the `diff` and `nginx -t` lines after deleting the only command that installs
# the template. And ORDER is load-bearing twice over: collect before the app restarts, and
# validate after the config is in place.
COLLECTSTATIC = re.compile(r"manage\.py\s+collectstatic\b")
APP_RESTART = re.compile(r"systemctl\s+(?:restart|enable --now)\s+octonomy\b")
INSTALL_NGINX_TEMPLATE = re.compile(
    r"cp\s+\S*deploy/systemd/nginx-octonomy\.conf\s+\S*/etc/nginx/\S+"
)
NGINX_VALIDATE = re.compile(r"nginx\s+-t\b")
NGINX_RELOAD = re.compile(r"systemctl\s+reload\s+nginx\b")


def _first_index(pattern, text):
    """Where ``pattern`` first appears, or None."""

    match = pattern.search(text)
    return match.start() if match else None


def test_the_vps_install_collects_static_before_starting_the_app(scripts_dir):
    """A fresh VPS install that starts the service without collecting serves an admin
    console and a browsable API that cannot render."""

    install, _ = _option_c(scripts_dir)

    collect = _first_index(COLLECTSTATIC, install)
    start = _first_index(APP_RESTART, install)

    assert collect is not None, "Option C's install steps no longer run manage.py collectstatic"
    assert start is not None, "Option C's install steps no longer start the service"
    assert collect < start, (
        "Option C collects static AFTER starting the app. WhiteNoise indexes STATIC_ROOT at "
        "process start, so the running workers never see those files"
    )


def test_the_vps_upgrade_collects_static_before_restarting_the_app(scripts_dir):
    """The omission that actually bit, plus the ordering that makes fixing it work.

    STATIC_ROOT stays non-empty across an upgrade, so octonomy.W002 says nothing and the
    operator serves the previous release's assets. Collecting after the restart is barely
    better: the workers disagree until they recycle.
    """

    _, upgrade = _option_c(scripts_dir)

    assert upgrade, f"Option C has no **Upgrades:** block in {DEPLOYMENT_DOC}"
    collect = _first_index(COLLECTSTATIC, upgrade)
    restart = _first_index(APP_RESTART, upgrade)

    assert collect is not None, "Option C's upgrade steps no longer run manage.py collectstatic"
    assert restart is not None, "Option C's upgrade steps no longer restart the app"
    assert collect < restart, "Option C collects static AFTER the restart, which does nothing"


def test_the_vps_upgrade_installs_and_validates_the_nginx_template(scripts_dir):
    """Changing the template in the checkout does nothing to the copy under /etc/nginx.

    Asserts the command that actually installs it — a `diff` and an `nginx -t` left behind
    after deleting the `cp` kept an earlier version of this test green — and that validation
    comes after the install, since `nginx -t` reads the active config.
    """

    _, upgrade = _option_c(scripts_dir)

    install = _first_index(INSTALL_NGINX_TEMPLATE, upgrade)
    validate = _first_index(NGINX_VALIDATE, upgrade)
    reload_ = _first_index(NGINX_RELOAD, upgrade)

    assert install is not None, (
        "Option C's upgrade no longer copies nginx-octonomy.conf into /etc/nginx; an "
        "existing host keeps whatever /static/ handling it was installed with"
    )
    assert validate is not None, "Option C's upgrade no longer runs nginx -t"
    assert reload_ is not None, "Option C's upgrade no longer reloads nginx"
    assert install < validate, "Option C validates nginx before installing the new config"
    # The FIRST validation has to sit on the success path, ahead of the first reload. An
    # earlier version of this test was satisfied by the `nginx -t` inside the rollback
    # branch — which only runs after a reload has already failed, so deleting the primary
    # check left an ordinary successful upgrade with no pre-reload validation at all.
    # `systemctl reload` can also return success while nginx rejects the config, leaving an
    # invalid file on disk to surface at the next restart.
    assert validate < reload_, (
        "Option C reloads nginx before validating it; the only remaining nginx -t is on the "
        "rollback path, which a successful upgrade never reaches"
    )
