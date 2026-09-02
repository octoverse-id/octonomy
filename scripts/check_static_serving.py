#!/usr/bin/env python
"""Assert every deploy channel still has a static-serving story.

THIS GATE DETECTS DRIFT, NOT CORRECTNESS. It reads configuration files. A file claiming a
static story proves nothing about an HTTP response, and treating a green run here as
evidence that assets are reachable is exactly the category error that let issue #142 ship:
the assets were in the image the whole time and simply unreachable. Correctness is asserted
by ``scripts/assert-static-served.sh``, which fetches real URLs from a real container in
CI. What this gate is for is the slower failure — a channel quietly losing its story during
an unrelated edit, months after anyone remembers to check by hand.

Two checks per file.

**A static-serving marker must be present.** There is no permissive default: a file with
nothing recognisable fails, because a grep matching zero files stays green forever and
stops anyone from checking by hand. Counts are deliberately not asserted — adding a
service or a comment must not break the gate.

  collectstatic       the channel gathers the assets (Dockerfile build step, runbook)
  whitenoise          the app serves them itself
  location /static/   an external server serves STATIC_ROOT (the systemd/nginx channel)
  <published image>   the channel runs the image, which does both of the first two
  manage.py check     the channel runs the boot check, so octonomy.W002 tells the operator
                      when STATIC_ROOT is empty. This is the ONLY automated static signal
                      the systemd unit has — it collects nothing itself and fronts nothing,
                      so losing that line leaves a VPS operator with no warning at all.

Markers only count outside comments. That is load-bearing rather than tidiness: the
Dockerfile explains ``collectstatic`` at length in prose, so matching raw text let someone
delete the operative ``RUN`` line and keep this gate green off the documentation for it, or
satisfy it outright with ``command: echo ok # collectstatic used to run here``. systemd
also treats a leading ``;`` as a comment. All three were real before being closed.

**No mount may hide the baked assets.** The assets live inside the image with no volume; a
bind mount or emptyDir over ``/app`` or ``/app/staticfiles`` hides them and every
``/static/*`` request starts 404ing, with nothing in the YAML that looks wrong.

This half parses the YAML rather than matching text, and the history is the argument for
it: three successive review rounds each found another valid spelling a line-oriented regex
missed — flow style, ``:Z`` mount options, a target that is not the last field on its line,
whitespace before the key's colon — and the widening needed to catch them started reporting
literal paths like ``target: /app,`` as mounts. Compose and Kubernetes mounts are structured
data; reading them as structured data ends that class instead of playing it out. Only
``.yaml``/``.yml`` files are inspected, because a Dockerfile, a systemd unit and an nginx
conf cannot declare a container mount at all.

The dev ``docker-compose.yml`` deliberately mounts the source over ``/app`` and is NOT
scanned: it runs ``runserver`` with ``DEBUG=true``, where static resolves through the
staticfiles finders rather than STATIC_ROOT.

Usage: check_static_serving.py FILE [FILE...]
Exit codes: 0 ok | 1 violations found | 2 usage
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

IMAGE = "ghcr.io/octoverse-id/octonomy"

# The directory the image bakes assets into, and anything beneath it.
BAKED_ASSET_PATHS = ("/app", "/app/staticfiles")

# Keys whose value is a mount destination. Asking for them by name is what makes this
# exact — no guessing from punctuation about whether a line is flow style or a literal path.
#
# `mountPath` is unambiguous: it exists only inside a Kubernetes volumeMounts entry.
# `target` is not — Compose long syntax uses it for the container path, but `target` is also
# a perfectly ordinary label or environment key, so it counts only inside a `volumes:`
# collection. Without that scoping, `labels: {target: /app}` reads as a mount.
UNAMBIGUOUS_MOUNT_KEYS = ("mountPath",)
VOLUME_SCOPED_MOUNT_KEYS = ("target",)

MARKERS = (
    ("collectstatic", re.compile(r"collectstatic", re.IGNORECASE)),
    ("whitenoise", re.compile(r"whitenoise", re.IGNORECASE)),
    ("location-static", re.compile(r"location\s+/static/")),
    ("published-image", re.compile(re.escape(IMAGE))),
    ("boot-check", re.compile(r"manage\.py check")),
)

# A '#' opens a comment only when it starts the line or whitespace precedes it — YAML's own
# rule, and it holds for the Dockerfile, systemd unit and nginx conf too. A leading ';' is a
# systemd comment.
WHOLE_LINE_COMMENT = re.compile(r"^\s*[#;]")
TRAILING_COMMENT = re.compile(r"\s#.*$")


def uncommented(text: str) -> str:
    """The file's content with comments removed, for marker matching."""

    kept = []
    for line in text.splitlines():
        if WHOLE_LINE_COMMENT.match(line):
            continue
        kept.append(TRAILING_COMMENT.sub("", line))
    return "\n".join(kept)


def markers_in(text: str) -> list[str]:
    return [name for name, pattern in MARKERS if pattern.search(text)]


def _short_syntax_target(entry: str) -> str | None:
    """The destination of a Compose short-syntax mount, e.g. ``./src:/app/staticfiles:ro``.

    Compose reads the second colon-separated field as the container path; the first is the
    host path or named volume and the optional third is the mount options.
    """

    parts = entry.split(":")
    return parts[1] if len(parts) >= 2 else None


def mount_targets(node: object, under_volumes: bool = False) -> list[str]:
    """Every container path anything in this document mounts over.

    Walks the whole document rather than the specific Compose/Kubernetes schema paths, so a
    mount stays visible however it is nested — a Deployment inside a List, a Compose
    override fragment, an unfamiliar future key.
    """

    found: list[str] = []

    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(value, str) and (
                key in UNAMBIGUOUS_MOUNT_KEYS or (under_volumes and key in VOLUME_SCOPED_MOUNT_KEYS)
            ):
                found.append(value)
            # Scoped to a `volumes:` collection, not latched onward: both Compose and
            # Kubernetes put the destination directly inside one (the list branch below
            # carries the flag across the intervening list), and latching would start
            # reading an unrelated nested `target` as a mount.
            found.extend(mount_targets(value, under_volumes=(key == "volumes")))
    elif isinstance(node, list):
        for item in node:
            # A bare string in a `volumes:` list is Compose short syntax. Elsewhere a
            # string is just a string, so this stays scoped to where it means a mount.
            if under_volumes and isinstance(item, str):
                target = _short_syntax_target(item)
                if target:
                    found.append(target)
            found.extend(mount_targets(item, under_volumes=under_volumes))

    return found


def hides_baked_assets(target: str) -> bool:
    """True when mounting at ``target`` would hide the image's collected static tree."""

    # Trailing slashes are cosmetic in a mount path, and a mount at /app/staticfiles/admin
    # hides that subtree just as effectively as the whole directory.
    normalised = "/" + target.strip().strip("/")
    return any(
        normalised == baked or normalised.startswith(baked + "/") for baked in BAKED_ASSET_PATHS
    )


def shadowing_mounts(path: Path, text: str) -> list[str]:
    """Mount targets in ``text`` that hide the baked assets, or [] for non-YAML files."""

    if path.suffix not in {".yaml", ".yml"}:
        return []

    # safe_load_all: a manifest may hold several documents, and safe_ rather than full_
    # because this only ever reads configuration, never constructs objects from it.
    documents = yaml.safe_load_all(text)
    return [t for document in documents for t in mount_targets(document) if hides_baked_assets(t)]


def check(path_name: str) -> list[str]:
    """Problems with one file. Empty means it still declares how static is served."""

    path = Path(path_name)
    if not path.is_file():
        return [
            f"{path_name}: no such file — it was renamed or removed, "
            "and this gate stopped checking it"
        ]

    text = path.read_text()
    effective = uncommented(text)

    markers = markers_in(effective)
    if not markers:
        return [
            f"{path_name}: no static-serving marker outside comments — "
            "this channel has lost its static story"
        ]

    try:
        shadows = shadowing_mounts(path, text)
    except yaml.YAMLError as exc:
        return [
            f"{path_name}: is not parseable as YAML, so its mounts cannot be checked"
            f" ({exc.__class__.__name__})"
        ]

    if shadows:
        listed = ", ".join(sorted(set(shadows)))
        return [
            f"{path_name}: mounts over {listed}, which hides the static assets baked into the image"
        ]

    print(f"ok    {path_name}: {' '.join(markers)}")
    return []


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: check_static_serving.py FILE [FILE...]", file=sys.stderr)
        return 2

    failures = []
    for path_name in argv:
        for problem in check(path_name):
            print(f"FAIL  {problem}")
            failures.append(problem)

    if failures:
        print(f"check-static-serving FAILED: {len(failures)} problem(s)", file=sys.stderr)
        return 1

    print("check-static-serving OK: every deploy channel still declares how static is served")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
