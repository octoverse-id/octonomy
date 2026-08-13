#!/usr/bin/env bash
# Assert that a release tag names the commit where its version FIRST appears — the
# release PR's merge commit — and not a later commit that merely still carries it.
#
# The hole this closes. publish-image.yml gates a release on three things: the commit
# is on main, it has a green CI run on main, and the tag version equals the pyproject
# version at that commit. Only release PRs bump the version, so *every* commit merged
# after the release PR satisfies all three. Tag a docs merge, a bug fix or a feature
# and the immutable :X.Y.Z is built from the wrong tree while every existing check
# reports success. Demonstrated on this repository's own history: b21f690 (the v3.1.0
# release merge, which tag object 7032af7 points at) and b1b27bb (a docs merge two
# commits later) both read version = "3.1.0", so tagging v3.1.0 at b1b27bb would have
# shipped the wrong tree.
#
# COMMIT is dereferenced with ^{commit}, so passing an annotated tag name or tag-object
# SHA resolves to the commit it points at rather than failing.
#
# Nothing downstream catches it either — docs/release.md step 6(a) only asks whether a
# successful publish run exists for that commit, which it would. And :X.Y.Z is
# immutable by design, so the only recovery is to burn the version and cut a patch.
#
# The discriminator is the first parent: on the release merge it still carries the
# PREVIOUS version, and on any later commit it already carries this one.
#
# FIRST PARENT ONLY, and it is load-bearing. On a merge commit the second parent is the
# release branch, which already has the bump — comparing against ^2 would refuse every
# real release.
#
# Usage: check-release-merge.sh VERSION [COMMIT] [REPO_DIR]
#
#   VERSION   the version being published, "3.1.0" or "v3.1.0"
#   COMMIT    the commit the tag points at (default HEAD)
#   REPO_DIR  run against this checkout instead of the current directory. Exists so
#             tests can drive real merge commits — the ^1 behaviour above cannot be
#             tested without them — and so a maintainer can check another worktree.
#
# Fails CLOSED on anything it cannot determine (exit 3). A version this script cannot
# reason about must not be published on the strength of its silence.
#
# Exit codes: 0 ok | 1 not the release merge | 2 usage | 3 cannot determine

set -euo pipefail

die() {
  echo "check-release-merge: $1" >&2
  exit "${2:-1}"
}

if [ "$#" -lt 1 ] || [ "$#" -gt 3 ]; then
  echo "usage: check-release-merge.sh VERSION [COMMIT] [REPO_DIR]" >&2
  exit 2
fi

[[ $1 =~ ^v?[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "'$1' is not an X.Y.Z version" 2
version=${1#v}
commit=${2:-HEAD}

if [ "$#" -eq 3 ]; then
  [ -d "$3" ] || die "repo dir '$3' does not exist" 2
  cd "$3"
fi

# Read the `version = "X.Y.Z"` line out of pyproject.toml at a given commit. `^version`
# is anchored so ruff's `target-version` cannot be mistaken for the project version —
# the same match the Makefile's version-check uses.
version_at() {
  local rev=$1 blob
  blob=$(git show "$rev:pyproject.toml" 2>/dev/null) || return 1
  printf '%s\n' "$blob" | sed -nE 's/^version = "([^"]+)"$/\1/p' | head -n1
}

sha=$(git rev-parse --verify "$commit^{commit}" 2>/dev/null) ||
  die "'$commit' is not a commit in this repository" 3

current=$(version_at "$sha") || die "pyproject.toml does not exist at $sha" 3
[ -n "$current" ] || die "could not read a version from pyproject.toml at $sha" 3

if [ "$current" != "$version" ]; then
  echo "FAIL  $sha carries version $current, not $version"
  die "the tag and the tree disagree — tag the commit that bumped to $version" 1
fi

# A root commit cannot be "a later commit that still carries the version", because
# there is nothing before it. Allow it: this is the first release.
if ! parent=$(git rev-parse --verify "$sha^1" 2>/dev/null); then
  echo "ok    $sha is a root commit — $version first appears here"
  exit 0
fi

# pyproject.toml absent in the parent means this commit introduced it, so the version
# first appears here by definition.
if ! previous=$(version_at "$parent"); then
  echo "ok    $sha introduced pyproject.toml — $version first appears here"
  exit 0
fi

[ -n "$previous" ] ||
  die "could not read a version from pyproject.toml at parent $parent" 3

if [ "$previous" = "$version" ]; then
  echo "FAIL  $sha is not the release merge for $version"
  echo "      its first parent $parent already carries $version"
  die "tag the release PR's merge commit, not a commit merged after it" 1
fi

echo "ok    $sha is the release merge for $version (first parent had $previous)"
