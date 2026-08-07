#!/usr/bin/env bash
# Decide which registry tags a release version should be promoted to.
#
# Why this exists: docker/metadata-action's `latest=auto` sets `:latest` on *any*
# non-prerelease semver tag event, without comparing it to the other tags in the
# repository. Tagging a backport `v2.0.2` while `v3.1.0` is the current release would
# move `:latest` backward and silently downgrade every deployment running
# `imagePullPolicy: Always`. publish-image.yml therefore runs metadata-action with
# `flavor: latest=false` and decides `:latest` here, by comparing the version being
# published against every release tag that exists.
#
# Usage: resolve-latest-tag.sh VERSION [TAG_LIST_FILE]
#
#   VERSION        the version being published — "3.1.0" or "v3.1.0"
#   TAG_LIST_FILE  a file of newline-separated git tags. Defaults to `git tag --list`.
#                  The argument exists so tests/tooling can drive the comparison
#                  without building a git repository per case.
#
# Prints one registry tag per line, in promotion order:
#
#   3.1.0
#   3.1
#   latest        <- only when no released version is greater than VERSION
#
# There is deliberately NO prerelease handling. publish-image.yml's trigger glob is
# `v[0-9]+.[0-9]+.[0-9]+`, so a prerelease tag cannot reach this script; rules for an
# unreachable case are rules nothing ever exercises.
#
# Exit codes: 0 ok | 2 usage/bad version | 3 no release tags found (fails closed)

set -euo pipefail

die() {
  echo "resolve-latest-tag: $1" >&2
  exit "${2:-1}"
}

if [ "$#" -lt 1 ] || [ "$#" -gt 2 ]; then
  echo "usage: resolve-latest-tag.sh VERSION [TAG_LIST_FILE]" >&2
  exit 2
fi

[[ $1 =~ ^v?[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "'$1' is not an X.Y.Z version" 2
version=${1#v}

if [ "$#" -eq 2 ]; then
  [ -r "$2" ] || die "tag list file '$2' is not readable" 2
  tag_source=$(cat -- "$2")
else
  tag_source=$(git tag --list)
fi

# Only exact vX.Y.Z tags participate. Anything else in the repository — `v3.1`,
# `v3.1.0-rc1`, a moved branch name — is ignored rather than guessed at.
releases=()
while IFS= read -r tag; do
  [[ $tag =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || continue
  releases+=("${tag#v}")
done <<<"$tag_source"

# Fail closed on an empty enumeration. A shallow checkout has no tags, so this script
# would otherwise see "nothing is greater than me" and hand out `:latest` on every
# release — including the backport this whole file exists to catch.
if [ "${#releases[@]}" -eq 0 ]; then
  die "no vX.Y.Z tags found — checkout has no tags (use actions/checkout with fetch-depth: 0)" 3
fi

# True when $1 is strictly greater than $2. `sort -V` would be one line, but its
# behaviour differs between coreutils builds and it silently sorts non-versions
# instead of rejecting them; explicit field arithmetic is what tests/tooling can pin
# down exactly. 10# forces base 10 so a zero-padded field like `08` is not read as
# octal.
version_gt() {
  local left right i l r
  IFS=. read -r -a left <<<"$1"
  IFS=. read -r -a right <<<"$2"
  for i in 0 1 2; do
    l=$((10#${left[i]}))
    r=$((10#${right[i]}))
    [ "$l" -gt "$r" ] && return 0
    [ "$l" -lt "$r" ] && return 1
  done
  return 1
}

newer_exists=0
for candidate in "${releases[@]}"; do
  if version_gt "$candidate" "$version"; then
    newer_exists=1
    break
  fi
done

printf '%s\n' "$version" "${version%.*}"
if [ "$newer_exists" -eq 0 ]; then
  printf '%s\n' latest
fi
