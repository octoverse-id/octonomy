#!/usr/bin/env bash
# Assert every published-image reference in the example configs and docs names a tag
# this repository actually publishes, at the version being released.
#
# The failure this guards against is a gate that passes by finding nothing. A grep
# matching zero files stays green forever, which is worse than having no gate at all
# because it stops anyone from checking by hand. So the contract is per-FILE presence:
# every file named on the command line must contain at least one reference. Counts are
# deliberately not asserted — adding a service to compose.yaml adds a reference and
# must not break the gate.
#
# Usage: check-image-refs.sh VERSION FILE [FILE...]
#
#   VERSION  the version the tree claims to be, "3.1.0" or "v3.1.0" (normally the
#            pyproject version, passed in by the Makefile's version-check target)
#
# Per reference, the tag is classified — there is no permissive default, because the
# whole point is that a typo like `:3.1.O` must not slip through:
#
#   latest, edge  documented moving tags; allowed anywhere, not version-checked
#   X.Y.Z         must equal VERSION, otherwise the reference is stale
#   anything else unknown tag -> fail
#
# Exit codes: 0 ok | 1 violations found | 2 usage

set -euo pipefail

IMAGE=ghcr.io/octoverse-id/octonomy

if [ "$#" -lt 2 ]; then
  echo "usage: check-image-refs.sh VERSION FILE [FILE...]" >&2
  exit 2
fi

if [[ ! $1 =~ ^v?[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "check-image-refs: '$1' is not an X.Y.Z version" >&2
  exit 2
fi
version=${1#v}
shift

# The image name is matched in full, so a database URL like
# postgres://octonomy:PASSWORD@host cannot be mistaken for an image reference.
pattern="${IMAGE//./\\.}:[A-Za-z0-9_.-]+"

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

  refs=$(grep -oE "$pattern" -- "$file" | sort -u || true)

  if [ -z "$refs" ]; then
    fail "$file: contains no $IMAGE reference"
    continue
  fi

  while IFS= read -r ref; do
    tag=${ref#"$IMAGE":}
    case "$tag" in
      latest | edge)
        echo "ok    $file: $ref (moving tag, not version-checked)"
        ;;
      *)
        if [[ $tag =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
          if [ "$tag" = "$version" ]; then
            echo "ok    $file: $ref"
          else
            fail "$file: $ref is stale — this tree is version $version"
          fi
        else
          fail "$file: $ref has an unrecognised tag '$tag' — expected X.Y.Z, latest, or edge"
        fi
        ;;
    esac
  done <<<"$refs"
done

if [ "$failures" -ne 0 ]; then
  echo "check-image-refs FAILED: $failures problem(s)" >&2
  exit 1
fi

echo "check-image-refs OK: every file references $IMAGE at $version, latest, or edge"
