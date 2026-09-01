#!/usr/bin/env bash
# Assert every deploy channel still has a static-serving story, and that none of them
# has grown a mount that hides the assets baked into the image.
#
# THIS GATE DETECTS DRIFT, NOT CORRECTNESS. It reads text. A file claiming a static story
# proves nothing about an HTTP response, and treating a green run here as evidence that
# assets are reachable is exactly the category error that let issue #142 ship: the assets
# were present in the image the whole time and simply unreachable. Correctness is asserted
# by scripts/assert-static-served.sh, which fetches real URLs from a real container in CI.
# What this gate is for is the slower failure — a channel quietly losing its story during
# an unrelated edit, months after anyone remembers to check by hand.
#
# Usage: check-static-serving.sh FILE [FILE...]
#
# Per FILE, at least one marker must be present. There is no permissive default: a file
# with nothing recognisable fails, because a grep that matches zero files stays green
# forever and stops anyone from checking by hand. Counts are deliberately not asserted —
# adding a service or a comment must not break the gate.
#
#   collectstatic       the channel gathers the assets (Dockerfile build step, runbook)
#   whitenoise          the app serves them itself
#   location /static/   an external server serves STATIC_ROOT (the systemd/nginx channel)
#   <published image>   the channel runs the image, which does both of the first two
#   manage.py check     the channel runs the boot check, so octonomy.W002 tells the
#                       operator when STATIC_ROOT is empty. This is the ONLY automated
#                       static signal the systemd unit has — it collects nothing itself
#                       and fronts nothing, so losing that line leaves a VPS operator with
#                       no warning at all.
#
# Separately, an image-based channel fails if it mounts anything over /app or
# /app/staticfiles. The assets are baked into the image with no volume; a bind mount or
# emptyDir there hides them and every /static/* request starts 404ing, with nothing in the
# YAML that looks wrong. The dev docker-compose.yml deliberately does this and is NOT
# scanned: it runs `runserver` with DEBUG=true, where static resolves through the
# staticfiles finders rather than STATIC_ROOT.
#
# Exit codes: 0 ok | 1 violations found | 2 usage

set -euo pipefail

IMAGE=ghcr.io/octoverse-id/octonomy

if [ "$#" -lt 1 ]; then
  echo "usage: check-static-serving.sh FILE [FILE...]" >&2
  exit 2
fi

failures=0

fail() {
  echo "FAIL  $1"
  failures=$((failures + 1))
}

for file in "$@"; do
  if [ ! -f "$file" ]; then
    fail "$file: no such file — it was renamed or removed, and this gate stopped checking it"
    continue
  fi

  # Comments are stripped before anything is matched, and that is load-bearing rather
  # than tidiness. Every format scanned here — Dockerfile, YAML, systemd unit, nginx conf
  # — comments with '#', and the Dockerfile's own comment block explains collectstatic at
  # length. Matching raw text let someone delete the real `RUN ... collectstatic` line and
  # keep this gate green off the prose describing it, which is precisely the "passes while
  # broken" shape this gate exists to prevent. Verified: before this, deleting that line
  # left the gate reporting ok. `grep -n` runs first so reported line numbers stay true to
  # the file.
  effective=$(grep -nvE '^[[:space:]]*#' -- "$file" || true)

  markers=""
  grep -qi 'collectstatic' <<<"$effective" && markers="$markers collectstatic"
  grep -qi 'whitenoise' <<<"$effective" && markers="$markers whitenoise"
  grep -qE 'location[[:space:]]+/static/' <<<"$effective" && markers="$markers location-static"
  grep -qF "$IMAGE" <<<"$effective" && markers="$markers published-image"
  grep -qE 'manage\.py check' <<<"$effective" && markers="$markers boot-check"

  if [ -z "$markers" ]; then
    fail "$file: no static-serving marker on a non-comment line — this channel has lost its static story"
    continue
  fi

  # Normalise before matching, because every spelling below is valid YAML for the same
  # mount and each one hides the assets identically:
  #
  #   - ./x:/app                    - "./x:/app/staticfiles:ro"      mountPath: "/app"
  #   - type: bind                  mountPath: /app/staticfiles/     target: /app/
  #     target: /app/staticfiles
  #
  # So: drop trailing comments, drop quotes, collapse trailing slashes. Matching the raw
  # line instead would let a reviewer's preferred quoting style walk straight through a
  # gate whose whole purpose is to notice this.
  normalised=$(sed -e 's/[[:space:]]#.*$//' -e 's/["'"'"']//g' -e 's#/\+$##' <<<"$effective")

  # /app itself, or anything underneath it — a mount at /app/staticfiles/admin hides that
  # subtree just as effectively. The character class after /app is what keeps /apple out.
  #
  # `^[0-9]+:` is required, not decorative: these lines still carry grep -n's line-number
  # prefix, so a line whose content is bare `/app` arrives as `4:/app` and would otherwise
  # satisfy the `:/app` alternative all by itself. Anchoring the prefix forces the colon
  # that matters to come from the content.
  shadow=$(grep -E '^[0-9]+:.*(:|(mountPath|target):[[:space:]]*)/app(/[^:[:space:]]*)*(:[a-z,]+)?[[:space:]]*$' <<<"$normalised" || true)
  if [ -n "$shadow" ]; then
    fail "$file: mounts over /app, which hides the static assets baked into the image:"
    echo "$shadow" | sed 's/^/        /'
    continue
  fi

  echo "ok    $file:$markers"
done

if [ "$failures" -ne 0 ]; then
  echo "check-static-serving FAILED: $failures problem(s)" >&2
  exit 1
fi

echo "check-static-serving OK: every deploy channel still declares how static is served"
