#!/usr/bin/env bash
# Export this repository's rulesets to .github/rulesets/*.json as a reviewable record.
#
# Why this exists as a script rather than a documented one-liner: the value of the
# committed JSON is that a change to a ruleset shows up as a readable diff. That only
# holds if every export applies the SAME field filter and the SAME key order —
# otherwise re-exporting reformats the file and the real change drowns in noise. This
# pins both.
#
# It is NOT a gate. It makes no pass/fail decision and has no tests, unlike
# scripts/check-*.sh, which do and therefore must. Detecting that a live ruleset has
# drifted from these files needs a credential this repo does not have — `administration`
# is not a GITHUB_TOKEN permission scope — and is tracked as DEP-4 in TODOS.md.
#
# Direction of truth: the LIVE ruleset is authoritative; these files are the reviewed
# record of it. To change a ruleset, change it in the GitHub UI or via the API, then
# re-run this and commit the diff. Editing the JSON alone changes nothing.
#
# Usage: export-rulesets.sh [OUT_DIR]        (default .github/rulesets)
#
# Requires: gh authenticated with repo admin access (reading rulesets needs it).
#
# Exit codes: 0 ok | 2 usage | 3 cannot read rulesets

set -euo pipefail

REPO=${REPO:-octoverse-id/octonomy}

die() {
  echo "export-rulesets: $1" >&2
  exit "${2:-1}"
}

if [ "$#" -gt 1 ]; then
  echo "usage: export-rulesets.sh [OUT_DIR]" >&2
  exit 2
fi

out_dir=${1:-.github/rulesets}
mkdir -p "$out_dir"

command -v jq >/dev/null 2>&1 || die "jq is required" 3

# Only the fields a create/update accepts. Everything else — id, node_id, created_at,
# updated_at, current_user_can_bypass, source, source_type, _links — is assigned by the
# server and would make every export a spurious diff.
#
# bypass_actors is kept deliberately and listed FIRST after the identity fields: it is
# the field that matters most and the easiest one to weaken quietly, so it should be
# impossible to miss when reading the diff.
FILTER='{name, target, enforcement, bypass_actors, conditions, rules}'

ids=$(gh api "repos/$REPO/rulesets" --jq '.[].id') ||
  die "could not list rulesets for $REPO (repo admin access required)" 3
[ -n "$ids" ] || die "no rulesets found for $REPO — nothing to export" 3

count=0
for id in $ids; do
  body=$(gh api "repos/$REPO/rulesets/$id") || die "could not read ruleset $id" 3

  # File name from the ruleset name: lowercase, non-alphanumerics collapsed to dashes.
  # Stable across exports because it derives only from the name.
  slug=$(printf '%s' "$body" | jq -r '.name' |
    tr '[:upper:]' '[:lower:]' | sed -E 's/[^a-z0-9]+/-/g; s/^-+|-+$//g')
  [ -n "$slug" ] || die "ruleset $id has a name that produced an empty file name" 3

  printf '%s\n' "$body" | jq -S "$FILTER" >"$out_dir/$slug.json"
  echo "exported $id -> $out_dir/$slug.json"
  count=$((count + 1))
done

echo "export-rulesets OK: $count ruleset(s) written to $out_dir"
