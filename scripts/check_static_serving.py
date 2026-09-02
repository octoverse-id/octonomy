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

import posixpath
import re
import sys
from pathlib import Path

import yaml

IMAGE = "ghcr.io/octoverse-id/octonomy"

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


# Where a mount destination can appear, and how to read one out. Naming the collections is
# what keeps this exact: `target` and `mountPath` are also perfectly ordinary label,
# annotation and driver-option keys, so each counts only as a DIRECT item of the collection
# that gives it mount meaning.
#
#   volumes       Compose service mounts. Mapping entries carry `target`; bare strings are
#                 short syntax, `[source:]target[:options]`.
#   configs       Compose. Mapping entries carry `target`. A config or secret mounted over a
#   secrets       file REPLACES it, so one aimed at staticfiles.json swaps the production
#                 manifest out from under WhiteNoise before Django even starts.
#   tmpfs         Compose. Bare strings are `target[:options]` — no source to strip. A tmpfs
#                 over the collected tree is the quietest break available: the container
#                 starts normally and every asset 404s off an empty in-memory filesystem.
#   volumeMounts  Kubernetes. Mapping entries carry `mountPath`.
#
# Direct items only, deliberately. Latching the collection through every descendant made a
# top-level named-volume declaration's `driver_opts: {target: /app}` — an opaque driver
# option, not a destination — read as a mount, and would do the same to a Kubernetes CSI
# `volumeAttributes`. A false alarm is how a gate like this gets deleted.
MOUNT_LISTS = {
    "volumes": {"key": "target", "string": "short"},
    "configs": {"key": "target", "string": None},
    "secrets": {"key": "target", "string": None},
    "tmpfs": {"key": None, "string": "path"},
    "volumeMounts": {"key": "mountPath", "string": None},
}

# `${VAR:-/app}` / `${VAR-/app}`: Compose substitutes the default when VAR is unset, which
# is the normal state of a committed example file, so the default is what a reader deploys.
INTERPOLATION_WITH_DEFAULT = re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*:?-([^}]*)\}")

# A leading `C:` is a Windows drive letter, not the source/target separator.
WINDOWS_DRIVE = re.compile(r"^[A-Za-z]$")

# The collected static tree. A mount at an ANCESTOR of it hides it; so does the tree itself
# or anything inside it. A sibling under /app — `./logs:/app/logs`, say — hides nothing.
BAKED_ASSETS = "/app/staticfiles"


def substitute(text: str) -> str | None:
    """``text`` with resolvable interpolation expanded, or None if it stays unknowable.

    Runs BEFORE any colon splitting, and that order is the whole point. Splitting first
    tears `${TARGET:-/app}` at the operator's own colon, leaving a `${TARGET` fragment that
    no "is this still interpolated?" test recognises — so `./src:${TARGET:-/app}`, a bind
    mount straight onto /app, read as an ordinary harmless path.

    Only the default-value forms can be resolved. `${VAR}`, `${VAR:?err}` and `${VAR:+alt}`
    depend on the deploying environment, so they stay unknowable and are reported as such
    rather than quietly passing.
    """

    expanded = INTERPOLATION_WITH_DEFAULT.sub(lambda match: match.group(1), text).strip()
    return None if "$" in expanded else expanded


def _short_syntax_target(entry: str) -> str | None:
    """The destination of a Compose short-syntax ``volumes`` entry.

    Three shapes, and the lone-path one is easy to overlook: ``- /app`` is an ANONYMOUS
    VOLUME mounted at /app, which hides the collected tree exactly like a bind mount does.

        /app                    -> /app             (anonymous volume)
        ./src:/app/staticfiles  -> /app/staticfiles
        ./src:/app:ro           -> /app
        C:\\work:/app            -> /app             (first colon is a drive letter)
    """

    parts = entry.split(":")
    if len(parts) == 1:
        return parts[0]
    if len(parts) >= 3 and WINDOWS_DRIVE.match(parts[0]):
        return parts[2]
    return parts[1]


def _destinations(collection: object, spec: dict) -> list[tuple[str | None, str]]:
    """``(resolved destination or None, original text)`` for one mount collection's items."""

    # `tmpfs: /app` is as valid as a one-item list.
    items = collection if isinstance(collection, list) else [collection]

    found: list[tuple[str | None, str]] = []
    for item in items:
        if isinstance(item, str):
            if spec["string"] is None:
                continue
            resolved = substitute(item)
            if resolved is None:
                found.append((None, item))
            elif spec["string"] == "short":
                found.append((_short_syntax_target(resolved), item))
            else:
                # tmpfs: everything after the first colon is mount options.
                found.append((resolved.split(":")[0], item))
        elif isinstance(item, dict) and spec["key"]:
            value = item.get(spec["key"])
            if isinstance(value, str):
                found.append((substitute(value), value))

    return found


def mount_targets(node: object) -> list[tuple[str | None, str]]:
    """Every container path anything in this document mounts over.

    Walks the whole document looking for the collections above rather than following the
    Compose/Kubernetes schema from the root, so a mount stays visible however it is nested —
    a Deployment inside a List, a Compose override fragment, an unfamiliar future key.
    """

    found: list[tuple[str | None, str]] = []

    if isinstance(node, dict):
        for key, value in node.items():
            spec = MOUNT_LISTS.get(key)
            if spec:
                found.extend(_destinations(value, spec))
            found.extend(mount_targets(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(mount_targets(item))

    return found


def normalise(target: str) -> str:
    """``target`` as Compose resolves it — `/srv/../app` really does land on /app."""

    return posixpath.normpath("/" + target.strip("/"))


def hides_baked_assets(normalised: str) -> bool:
    """True when mounting at ``normalised`` would hide the image's collected static tree."""

    if normalised == "/":
        return True
    return (
        normalised == BAKED_ASSETS
        # An ancestor mount covers the tree...
        or BAKED_ASSETS.startswith(normalised + "/")
        # ...and so does a mount inside it. A sibling like /app/logs does not.
        or normalised.startswith(BAKED_ASSETS + "/")
    )


def shadowing_mounts(path: Path, text: str) -> tuple[list[str], list[str]]:
    """``(targets that hide the assets, targets whose destination is unknowable)``.

    Non-YAML files yield nothing: a Dockerfile, systemd unit or nginx conf cannot declare a
    container mount at all, so there is nothing to check and no false-positive surface.
    """

    if path.suffix.lower() not in {".yaml", ".yml"}:
        return [], []

    # safe_load_all: a manifest may hold several documents, and safe_ rather than full_
    # because this only ever reads configuration, never constructs objects from it.
    raw = [d for document in yaml.safe_load_all(text) for d in mount_targets(document)]

    shadows: list[str] = []
    unverifiable: list[str] = []
    for resolved, original in raw:
        if resolved is None:
            # Fail closed only where the destination genuinely cannot be determined. An
            # operator deserves to know the gate could not check it, rather than being told
            # everything is fine.
            unverifiable.append(original)
        else:
            # Report the normalised path: it is what the mount actually lands on, which is
            # the thing an operator needs to see.
            landed = normalise(resolved)
            if hides_baked_assets(landed):
                shadows.append(landed)
    return shadows, unverifiable


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
        shadows, unverifiable = shadowing_mounts(path, text)
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

    if unverifiable:
        listed = ", ".join(sorted(set(unverifiable)))
        return [
            f"{path_name}: mount destination {listed} interpolates a variable with no "
            "default, so this gate cannot tell whether it lands on the baked assets"
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
