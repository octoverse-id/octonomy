#!/usr/bin/env bash
# Assert a RUNNING Octonomy actually serves the static assets its own pages ask for.
#
# Compensating control for decision dec-805139c7. The pytest suite runs the plain
# staticfiles backend (config/settings_pytest.py), so most tests never resolve a
# `{% static %}` through staticfiles.json.
#
# WHAT THIS ESTABLISHES, precisely: the BUILT IMAGE carries a usable production manifest,
# and the four surfaces probed here — the admin login page, the DRF browsable API, and the
# Swagger UI and Redoc docs pages — render and serve the hashed assets they reference. For
# the docs pages it also establishes that they reference NOTHING off-box (#146). That is a
# packaging and global-collection guarantee.
#
# WHAT IT DOES NOT: it renders four pages, not every page. A changelist or form template
# referencing an uncollected asset still passes the suite (plain backend) and passes here
# (never rendered), and fails only for the operator who opens that page. Closing that would
# mean a session-scoped production collectstatic fixture, as config/settings_pytest.py
# describes. tests/admin/test_static_serving.py and tests/openapi/test_docs_assets.py cover
# these same surfaces under manifest storage in-process; what this adds is the real image,
# really built.
#
# Probing /static/admin/css/base.css would prove nothing. collectstatic writes BOTH the
# original and the hashed name, so the unhashed path returns 200 even when the manifest is
# broken. The only honest probe is the path a browser actually requests, so every URL
# checked below is extracted from HTML the running app produced.
#
# Usage: assert-static-served.sh BASE_URL
#
#   BASE_URL  a running instance, e.g. http://localhost:8000. It must have the admin
#             enabled (OCTONOMY_ADMIN_ENABLED=true) and DEBUG off — that is the
#             configuration this gate exists to protect.
#
# Exit codes: 0 ok | 1 an assertion failed | 2 usage

set -euo pipefail

if [ "$#" -ne 1 ]; then
  echo "usage: assert-static-served.sh BASE_URL" >&2
  exit 2
fi

base=${1%/}
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

# Every probe is bounded. The CI job's own 20-minute timeout is the wrong instrument for
# this: a regression that accepts the connection and then never finishes a response would
# hold the required `docker` job open until the job is cancelled, and a cancelled job skips
# the log-dumping step — turning a fast, legible failure into a slow opaque one.
CURL=(curl -sS --connect-timeout 5 --max-time 30 --max-filesize 5000000)

# An upper bound on how many assets a page may reference before this is treated as a
# problem in its own right. The four real pages reference 27 between them; a page emitting
# hundreds means a template regression, and probing them all sequentially is how a bounded
# per-request timeout still adds up to a job timeout. Fails loudly rather than truncating,
# because silently probing a subset is the sampling defect this loop exists to avoid.
MAX_ASSETS=200

# The count ceiling alone does NOT bound the work, which was the flaw in relying on it:
# 200 assets at the 30s per-request ceiling is 100 minutes, five times the docker job's 20,
# so a server completing each response just under the per-call limit could still be killed
# by the outer job timeout instead of failing here legibly. This is the actual bound.
#
# Checked before each probe rather than mid-flight, so the true ceiling is this budget plus
# one in-flight request (10s below) — about 130s against a 20-minute job. Deliberately not
# made exact: capping curl to the remaining time buys nothing here and reads worse.
# Overridable so the mechanism itself can be tested.
PROBE_BUDGET_SECONDS=${OCTONOMY_PROBE_BUDGET_SECONDS:-120}

fail() {
  # ::error:: renders as an annotation on the CI job and as plain text anywhere else.
  echo "::error::$1" >&2
  exit 1
}

# --- The admin page must render at all ------------------------------------------------
# Under manifest storage a missing entry raises at render time, so the real failure mode
# is a 500 on the page, not a 404 on an asset. Check the page before the assets.
# `rc` is captured with `|| rc=$?` rather than tested via `if ! ...`: inside an `if !`
# branch $? is the negation's own 0, so the exit code would always be reported as zero.
rc=0
status=$("${CURL[@]}" -o "$work/admin.html" -w '%{http_code}' "$base/admin/login/") || rc=$?
if [ "$rc" -ne 0 ]; then
  fail "GET /admin/login/ did not complete (curl exit $rc). Is the container up?"
fi
if [ "$status" != "200" ]; then
  echo "--- first 2 KB of the response ---" >&2
  head -c 2048 "$work/admin.html" >&2 || true
  echo >&2
  fail "GET /admin/login/ returned $status (expected 200). A 500 here usually means collectstatic did not run, or ran against a different asset set than the templates reference."
fi
echo "ok    GET /admin/login/ -> 200"

# --- It must reference hashed assets --------------------------------------------------
# If nothing in the page is content-addressed, manifest storage is not in effect and every
# assertion below would be testing the wrong backend.
grep -oE '/static/[^"'"'"']+\.[0-9a-f]{8,}\.(css|js)' "$work/admin.html" |
  sort -u >"$work/admin-assets.txt" || true
if [ ! -s "$work/admin-assets.txt" ]; then
  fail "the rendered admin page references no hashed asset URL — the manifest staticfiles backend is not in effect, so this gate would be checking the wrong thing."
fi
echo "ok    rendered page references $(wc -l <"$work/admin-assets.txt" | tr -d ' ') hashed asset(s)"

# --- The browsable API must render, and reference hashed assets of its own --------------
# /static/rest_framework/* is needed even with the admin console disabled: DRF's
# BrowsableAPIRenderer is in DEFAULT_RENDERER_CLASSES, so it is never an optional surface.
#
# Probing the ORIGINAL /static/rest_framework/css/bootstrap.min.css here would repeat the
# very mistake this script rejects for the admin. collectstatic writes that file whether or
# not the manifest maps it, so it answers 200 while a real browsable-API render raises
# "Missing staticfiles manifest entry" resolving DRF's own {% static %} tags. Render the
# page and take the URL from what it produced.
#
# Accept: text/html selects BrowsableAPIRenderer. No token is sent, so DRF denies the
# request — but it denies it by RENDERING the browsable page, which is exactly the template
# under test. Any 2xx/4xx means the template rendered; a 5xx is the manifest failure.
rc=0
api_status=$("${CURL[@]}" -H 'Accept: text/html' -o "$work/api.html" \
  -w '%{http_code}' "$base/api/v2/tags") || rc=$?
if [ "$rc" -ne 0 ]; then
  fail "GET /api/v2/tags did not complete (curl exit $rc)"
fi
if [ "$api_status" -ge 500 ]; then
  echo "--- first 2 KB of the response ---" >&2
  head -c 2048 "$work/api.html" >&2 || true
  echo >&2
  fail "the browsable API returned $api_status — its template failed to render, which under manifest storage means a {% static %} tag resolved to an asset that was never collected."
fi
# 404 is called out separately from the other 4xx. A denial renders the template; a missing
# route renders Django's bare error page, which references no assets — so without this the
# failure would surface below as a confusing "no hashed rest_framework asset" instead of
# "the endpoint this gate probes has moved".
if [ "$api_status" = "404" ]; then
  fail "GET /api/v2/tags returned 404 — the route this gate probes has moved, so the browsable-API half is no longer being checked. Point it at a live endpoint."
fi
echo "ok    browsable API rendered ($api_status)"

# ALL hashed assets on this page, not only the rest_framework ones. Scoping the extractor
# to /static/rest_framework/ meant a broken asset from anywhere else on the same page — a
# project-level override, say — was never probed at all: status, type and CORS all skipped.
grep -oE '/static/[^"'"'"']+\.[0-9a-f]{8,}\.(css|js)' "$work/api.html" |
  sort -u >"$work/api-assets.txt" || true
# The rest_framework requirement stays, as a separate assertion: it is what proves the page
# really is the browsable renderer rather than some other 4xx that happens to carry assets.
if ! grep -q '/static/rest_framework/' "$work/api-assets.txt"; then
  fail "the rendered browsable API references no hashed rest_framework asset — either the manifest backend is not in effect or the page is not the browsable renderer."
fi
echo "ok    browsable API references $(wc -l <"$work/api-assets.txt" | tr -d ' ') hashed asset(s)"

# --- The docs UI: rendered, self-hosted, and reaching no third party ---------------------
# Added by #146, which moved the Swagger UI and Redoc bundles out of cdn.jsdelivr.net@latest
# and into the image. That makes /api/docs/* a static-dependent surface for the first time:
# under manifest storage an uncollected sidecar asset is a 500 on the page, not a fallback
# to the internet. It is also the surface most operators open first, and unlike the admin it
# is always on.
#
# Both templates are checked because they are different templates with different failure
# modes: Swagger's asset URLs come from three separate settings keys, while Redoc's page is
# rendered from an override template (octonomy.openapi.views.SelfHostedRedocView) whose only
# job is to drop the Google Fonts links the shipped one carries. A regression in either is
# invisible from the other.
#
# No token is needed: SERVE_PERMISSIONS leaves the docs public, like the schema they render.
: >"$work/docs-assets.txt"
for page in /api/docs/swagger/ /api/docs/redoc/; do
  rc=0
  status=$("${CURL[@]}" -o "$work/docs.html" -w '%{http_code}' "$base$page") || rc=$?
  if [ "$rc" -ne 0 ]; then
    fail "GET $page did not complete (curl exit $rc)"
  fi
  if [ "$status" != "200" ]; then
    echo "--- first 2 KB of the response ---" >&2
    head -c 2048 "$work/docs.html" >&2 || true
    echo >&2
    fail "GET $page returned $status (expected 200). A 500 means the docs UI bundles were not collected — check that drf_spectacular_sidecar is in INSTALLED_APPS and that collectstatic ran."
  fi

  # The air-gap assertion, and the reason this section exists at all. Any scheme, any host:
  # the contract is that these pages reach NOTHING off-box, and the last regression here was
  # Google Fonts rather than the CDN this issue was named for.
  #
  # Two greps rather than one pattern. A combined `(https?:)?//` would match the `//` line
  # comments in the Swagger init script that drf-spectacular inlines into the page, so
  # protocol-relative URLs are looked for only where they can actually be fetched from — a
  # src or href attribute.
  if grep -qE 'https?://' "$work/docs.html"; then
    grep -oE 'https?://[^"'"'"' <>]+' "$work/docs.html" | sort -u >&2
    fail "$page references the absolute URL(s) above. The docs UI must serve every asset from this deployment: no CDN, no font host, nothing an air-gapped install cannot reach."
  fi
  if grep -qE '(src|href)="//' "$work/docs.html"; then
    fail "$page references a protocol-relative URL, which resolves to a third-party host just as an absolute one does."
  fi

  # Extracted per page and asserted per page BEFORE appending to the accumulated list: a
  # shared file would let Swagger's assets satisfy Redoc's assertion, which is exactly the
  # regression (an override template silently reverting to the CDN) this is here to catch.
  grep -oE '/static/[^"'"'"']+\.[0-9a-f]{8,}\.(css|js)' "$work/docs.html" |
    sort -u >"$work/page-assets.txt" || true
  # Same reasoning as the rest_framework assertion above: a page carrying no hashed asset
  # at all would satisfy every check so far while proving nothing about the manifest.
  if ! grep -q '/static/drf_spectacular_sidecar/' "$work/page-assets.txt"; then
    fail "$page references no hashed drf_spectacular_sidecar asset — the bundles are not being served from this deployment, so the page is either still pointing at a CDN or not the docs UI."
  fi
  cat "$work/page-assets.txt" >>"$work/docs-assets.txt"
  echo "ok    GET $page -> 200, self-hosted, references $(wc -l <"$work/page-assets.txt" | tr -d ' ') hashed asset(s)"
done

# --- EVERY hashed asset those pages reference must actually be served -------------------
# Not just the first of each. Two reasons, both observed rather than theorised: in the
# shipped Unfold and DRF templates stylesheets precede scripts, so sampling one URL per
# page selected two .css files and the .js branch below never executed in a real CI run at
# all; and a page whose first stylesheet is fine while a later one 404s would have passed.
# These are localhost requests against an already-running container, so probing all of them
# costs milliseconds.
sort -u "$work/admin-assets.txt" "$work/api-assets.txt" "$work/docs-assets.txt" \
  >"$work/all-assets.txt"
asset_count=$(wc -l <"$work/all-assets.txt" | tr -d ' ')
if [ "$asset_count" -gt "$MAX_ASSETS" ]; then
  fail "the probed pages reference $asset_count hashed assets, over the $MAX_ASSETS ceiling. That is a template regression rather than a static-serving fault; probing them all sequentially would turn this gate into a job timeout."
fi
probe_started=$(date +%s)
while IFS= read -r url; do
  elapsed=$(($(date +%s) - probe_started))
  if [ "$elapsed" -ge "$PROBE_BUDGET_SECONDS" ]; then
    fail "still probing assets after ${elapsed}s, at or past the ${PROBE_BUDGET_SECONDS}s budget. Failing here with a diagnostic beats being killed by the job timeout without one."
  fi

  # curl's exit status is checked SEPARATELY from the HTTP code it reports. -w writes the
  # status and content type even when the transfer dies part-way through the body, so a
  # truncated asset would otherwise be parsed as a healthy "200 text/css".
  rc=0
  # --max-time 10 overrides the 30s the pages get (curl honours the last occurrence). These
  # are static files over loopback; ten seconds is already generous, and it keeps the worst
  # case for the whole loop inside the budget above.
  probe=$("${CURL[@]}" --max-time 10 -D "$work/headers.txt" -o /dev/null \
    -w '%{http_code} %{content_type}' "$base$url") || rc=$?
  if [ "$rc" -ne 0 ]; then
    fail "GET $url did not complete (curl exit $rc) — the response was truncated or timed out"
  fi
  status=${probe%% *}
  ctype=${probe#* }

  [ "$status" = "200" ] || fail "GET $url returned $status (expected 200)"

  # The type must match the EXTENSION, not merely land in a combined allowlist. A shared
  # allowlist accepted a stylesheet served as application/javascript, which a browser
  # refuses just as firmly as octet-stream — so the gate reported success on an asset no
  # page could actually use. Verified: with both hashed .css fixtures served as JS, the
  # shared-allowlist version exited 0.
  # Compare the media-type ESSENCE, not a prefix. `text/css*` as a glob also matches
  # `text/cssbogus`, which is a different subtype rather than a parameterised form of the
  # allowed one — so the gate could bless a type a browser refuses. Strip parameters at the
  # first ';', drop surrounding whitespace, lower-case (media types are case-insensitive),
  # then compare exactly.
  essence=${ctype%%;*}
  # Trim the EDGES only. `tr -d` over the whole value would fold `text / css` and
  # `text/<TAB>css` — both invalid, both rejected by a browser's MIME parser — into a
  # passing `text/css`, which is not the exact comparison this is meant to be.
  essence=${essence#"${essence%%[![:space:]]*}"}
  essence=${essence%"${essence##*[![:space:]]}"}
  essence=$(tr '[:upper:]' '[:lower:]' <<<"$essence")

  case "$url" in
    *.css)
      case "$essence" in
        text/css) ;;
        *) fail "GET $url is a stylesheet but was served as '$ctype' — a browser will not apply it" ;;
      esac
      ;;
    *.js)
      # Both spellings are current: WhiteNoise emits text/javascript, older mimetypes
      # tables and some proxies still say application/javascript.
      case "$essence" in
        text/javascript | application/javascript) ;;
        *) fail "GET $url is a script but was served as '$ctype' — a browser will not execute it" ;;
      esac
      ;;
    *)
      # Unreachable through the extraction regexes above, which only ever yield .css or
      # .js. Kept so widening them cannot silently skip this check.
      fail "GET $url has an extension this gate does not know how to type-check"
      ;;
  esac

  # Checked per asset, from the headers of the same response, rather than once against a
  # representative file. Note the contract: ABSENT, not merely "not a wildcard".
  # WHITENOISE_ALLOW_ALL_ORIGINS is False in the shipped settings and WhiteNoise then emits
  # no Access-Control-Allow-Origin at all; its own default is True, so a careless edit
  # re-opens every asset to any origin. CI runs the shipped configuration, so absence is the
  # exact assertion. A deployment that deliberately adopts a narrow CORS policy (STATIC_URL
  # on a CDN, say) is changing this contract and should change this line with it.
  if grep -qi '^access-control-allow-origin' "$work/headers.txt"; then
    grep -i '^access-control-allow-origin' "$work/headers.txt" >&2
    fail "GET $url carries an Access-Control-Allow-Origin header; the shipped configuration sets WHITENOISE_ALLOW_ALL_ORIGINS = False and should emit none"
  fi

  echo "ok    GET $url -> $status $ctype"
done <"$work/all-assets.txt"

echo "ok    no Access-Control-Allow-Origin on any static response"

echo "assert-static-served OK: $base renders its admin, browsable-API and docs pages, serves every hashed asset they reference, and reaches no third-party host"
