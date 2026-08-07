#!/usr/bin/env bash
# Resolve what digest a registry tag currently points at, so publish-image.yml can tell
# a fresh release from a partially-promoted one from a collision.
#
# Tag promotion is not atomic: `docker buildx imagetools create --tag :X.Y.Z --tag :X.Y`
# writes each tag as a separate registry operation, so `:3.1.0` can land and `:3.1`
# fail. A guard that asked "does :X.Y.Z exist?" would see the half-written release and
# refuse to let anyone finish it. So the question asked here is which *digest* the tag
# resolves to:
#
#   absent            -> nothing published under this tag; build, push, verify, promote
#   present <digest>  -> already published; the caller re-promotes this digest rather
#                        than rebuilding (a rebuild is never byte-identical, so
#                        rebuilding would change what a shipped version means)
#   match <digest>    -> the tag resolves to the digest the caller expected
#   conflict          -> the tag resolves to a DIFFERENT digest than expected; exit 1,
#                        naming both. Recovery is a manual package-version delete or a
#                        patch release — never an overwrite.
#
# Usage: check-tag-unpublished.sh IMAGE TAG [EXPECTED_DIGEST]
#
#   IMAGE            e.g. ghcr.io/octoverse-id/octonomy
#   TAG              e.g. 3.1.0
#   EXPECTED_DIGEST  optional sha256:... — supplying it turns `present` into
#                    `match` or `conflict`
#
# Environment:
#   GITHUB_TOKEN  optional; used as the registry password when exchanging for a pull
#                 token. Without it the exchange is anonymous, which only resolves
#                 public packages.
#   CURL          the curl to invoke; overridable so tests/tooling can drive every
#                 HTTP status without a network.
#
# This fails CLOSED. A 5xx, an auth failure, a rate limit, a missing digest header or an
# unparseable response all exit 3 — none of them are read as "not found". Treating an
# outage as absence is how you republish different bytes under a shipped version tag.
#
# Exit codes: 0 absent/present/match | 1 conflict | 2 usage | 3 registry error

set -euo pipefail

CURL=${CURL:-curl}

die() {
  echo "check-tag-unpublished: $1" >&2
  exit "${2:-1}"
}

if [ "$#" -lt 2 ] || [ "$#" -gt 3 ]; then
  echo "usage: check-tag-unpublished.sh IMAGE TAG [EXPECTED_DIGEST]" >&2
  exit 2
fi

image=$1
tag=$2
expected=${3:-}

case "$image" in
  */*/*) ;;
  *) die "'$image' is not a REGISTRY/NAMESPACE/NAME reference" 2 ;;
esac
registry=${image%%/*}
repository=${image#*/}

if [ -n "$expected" ] && [[ ! $expected =~ ^sha256:[0-9a-f]{64}$ ]]; then
  die "'$expected' is not a sha256:<64 hex> digest" 2
fi

# Step 1: exchange for a pull-scoped bearer token. --fail turns any non-2xx into a
# non-zero exit, so an auth or rate-limit response ends the run here rather than
# arriving later as an unauthenticated 404 that looks like "not published yet".
token_url="https://${registry}/token?service=${registry}&scope=repository:${repository}:pull"
auth_args=()
if [ -n "${GITHUB_TOKEN:-}" ]; then
  # Same username docker/login-action sends, so this exchange succeeds under exactly
  # the conditions the registry login does. GHCR ignores the username and validates
  # the password, but matching the working path removes a variable.
  auth_args=(--user "${GITHUB_ACTOR:-x-access-token}:${GITHUB_TOKEN}")
fi

if ! token_response=$("$CURL" --silent --show-error --fail --max-time 30 \
  ${auth_args[@]+"${auth_args[@]}"} "$token_url" 2>&1); then
  # GHCR answers 403 — not 404 — for a package that does not exist yet, and there is
  # no way to tell that apart from "we are not allowed to look". Both stop the run:
  # the alternative is reading an unknown as "nothing published here", which is how a
  # shipped version tag gets overwritten.
  die "could not obtain a pull token for ${image} (${token_response}).
                      A 403 here on a first release usually means the GHCR package does
                      not exist yet — publish :edge from main first, which creates it." 3
fi

token=$(printf '%s' "$token_response" |
  sed -n 's/.*"token"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -n1)
[ -n "$token" ] && [ "$token" != "null" ] || die "pull token response had no token field" 3

# Step 2: resolve the manifest. Headers and the final status code both come back on
# stdout so the whole exchange is one stream a fake curl can reproduce exactly. The
# Accept list covers OCI and Docker manifest lists — without it a multi-arch index can
# come back as a 404-shaped negotiation failure.
manifest_url="https://${registry}/v2/${repository}/manifests/${tag}"
if ! raw=$("$CURL" --silent --show-error --location --max-time 30 \
  --dump-header - --output /dev/null --write-out '\nHTTP_STATUS:%{http_code}' \
  --header "Authorization: Bearer ${token}" \
  --header "Accept: application/vnd.oci.image.index.v1+json" \
  --header "Accept: application/vnd.oci.image.manifest.v1+json" \
  --header "Accept: application/vnd.docker.distribution.manifest.list.v2+json" \
  --header "Accept: application/vnd.docker.distribution.manifest.v2+json" \
  "$manifest_url" 2>&1); then
  die "registry request for ${image}:${tag} failed (${raw})" 3
fi

status=$(printf '%s\n' "$raw" | sed -n 's/^HTTP_STATUS:\([0-9]*\)$/\1/p' | tail -n1)
[ -n "$status" ] || die "could not read an HTTP status from the registry response" 3

if [ "$status" = "404" ]; then
  echo absent
  exit 0
fi

if [ "$status" != "200" ]; then
  die "registry returned HTTP ${status} for ${image}:${tag} — refusing to treat that as unpublished" 3
fi

digest=$(printf '%s\n' "$raw" | tr -d '\r' |
  sed -n 's/^[Dd]ocker-[Cc]ontent-[Dd]igest:[[:space:]]*\(.*\)$/\1/p' | tail -n1)
[ -n "$digest" ] || die "registry returned 200 for ${image}:${tag} with no Docker-Content-Digest header" 3

if [ -z "$expected" ]; then
  echo "present ${digest}"
  exit 0
fi

if [ "$digest" = "$expected" ]; then
  echo "match ${digest}"
  exit 0
fi

echo "conflict" >&2
echo "check-tag-unpublished: ${image}:${tag} is already published as ${digest}," >&2
echo "                      but this run holds ${expected}." >&2
echo "                      Republishing different bytes under a shipped version is what a" >&2
echo "                      patch release is for. Recover by deleting that package version" >&2
echo "                      by hand, or by cutting the next patch." >&2
exit 1
