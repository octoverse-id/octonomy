from __future__ import annotations

import os

import pytest

IMAGE = "ghcr.io/octoverse-id/octonomy"
DIGEST = "sha256:" + "a" * 64
OTHER_DIGEST = "sha256:" + "b" * 64

# Stands in for curl. It reproduces exactly what the script consumes — a header dump
# followed by the --write-out status line — so the parsing under test is the real one,
# and every registry outcome (5xx, auth, rate limit, a 200 with no digest header) can
# be driven without a network.
FAKE_CURL = """#!/usr/bin/env bash
set -u
url=${@: -1}

if [ -n "${FAKE_CURL_LOG:-}" ]; then
  printf '%s\\n' "$*" >>"$FAKE_CURL_LOG"
fi

case "$url" in
  *"/token?"*)
    if [ -n "${FAKE_TOKEN_EXIT:-}" ]; then
      echo "curl: (22) The requested URL returned error: 503" >&2
      exit "$FAKE_TOKEN_EXIT"
    fi
    printf '%s\\n' "${FAKE_TOKEN_BODY:-{\\"token\\":\\"fake-pull-token\\"}}"
    ;;
  *"/manifests/"*)
    if [ -n "${FAKE_MANIFEST_EXIT:-}" ]; then
      echo "curl: (6) Could not resolve host" >&2
      exit "$FAKE_MANIFEST_EXIT"
    fi
    status=${FAKE_MANIFEST_STATUS:-200}
    printf 'HTTP/2 %s\\r\\n' "$status"
    printf 'content-type: application/vnd.oci.image.index.v1+json\\r\\n'
    if [ -n "${FAKE_DIGEST:-}" ]; then
      printf 'docker-content-digest: %s\\r\\n' "$FAKE_DIGEST"
    fi
    printf '\\r\\n'
    printf '\\nHTTP_STATUS:%s' "$status"
    ;;
  *)
    echo "fake curl: unexpected url $url" >&2
    exit 99
    ;;
esac
"""


@pytest.fixture
def registry(tmp_path):
    """Return a callable that runs the guard against a scripted registry."""

    fake = tmp_path / "fake-curl.sh"
    fake.write_text(FAKE_CURL)
    fake.chmod(0o755)

    def _env(**overrides: str) -> dict[str, str]:
        env = {k: v for k, v in os.environ.items() if k != "GITHUB_TOKEN"}
        env["CURL"] = str(fake)
        env.update(overrides)
        return env

    return _env


def test_absent_tag_reports_absent(run_script, registry):
    result = run_script(
        "check-tag-unpublished.sh", IMAGE, "3.1.0", env=registry(FAKE_MANIFEST_STATUS="404")
    )

    assert result.returncode == 0, result.output
    assert result.stdout.strip() == "absent"


def test_published_tag_reports_its_digest(run_script, registry):
    result = run_script(
        "check-tag-unpublished.sh", IMAGE, "3.1.0", env=registry(FAKE_DIGEST=DIGEST)
    )

    assert result.returncode == 0, result.output
    assert result.stdout.strip() == f"present {DIGEST}"


def test_expected_digest_that_matches_reports_match(run_script, registry):
    """The partial-promotion recovery path: `:3.1.0` landed, `:3.1` did not. A re-run
    finds the same digest and re-promotes it instead of refusing."""

    result = run_script(
        "check-tag-unpublished.sh", IMAGE, "3.1.0", DIGEST, env=registry(FAKE_DIGEST=DIGEST)
    )

    assert result.returncode == 0, result.output
    assert result.stdout.strip() == f"match {DIGEST}"


def test_a_different_digest_fails_and_names_both(run_script, registry):
    result = run_script(
        "check-tag-unpublished.sh", IMAGE, "3.1.0", OTHER_DIGEST, env=registry(FAKE_DIGEST=DIGEST)
    )

    assert result.returncode == 1
    assert DIGEST in result.stderr
    assert OTHER_DIGEST in result.stderr


def test_there_is_no_overwrite_escape_hatch(run_script, registry, scripts_dir):
    """A conflict is not something to make easy. Republishing different bytes under a
    shipped version is what a patch release is for."""

    source = (scripts_dir / "check-tag-unpublished.sh").read_text().lower()
    code = "\n".join(line for line in source.splitlines() if not line.lstrip().startswith("#"))

    assert "overwrite" not in code
    assert "--force" not in code


@pytest.mark.parametrize("status", ["401", "403", "429", "500", "502", "503"])
def test_registry_errors_fail_closed(run_script, registry, status):
    """None of these mean "not published yet". Reading an outage or a rate limit as
    absence is how a run republishes over a shipped version tag."""

    result = run_script(
        "check-tag-unpublished.sh", IMAGE, "3.1.0", env=registry(FAKE_MANIFEST_STATUS=status)
    )

    assert result.returncode == 3
    assert status in result.stderr
    assert "absent" not in result.stdout


def test_a_200_without_a_digest_header_fails_closed(run_script, registry):
    result = run_script("check-tag-unpublished.sh", IMAGE, "3.1.0", env=registry(FAKE_DIGEST=""))

    assert result.returncode == 3
    assert "Docker-Content-Digest" in result.stderr


def test_a_failed_token_exchange_fails_closed(run_script, registry):
    """Auth failing at the token endpoint must not arrive later as an unauthenticated
    404 that reads as unpublished."""

    result = run_script(
        "check-tag-unpublished.sh", IMAGE, "3.1.0", env=registry(FAKE_TOKEN_EXIT="22")
    )

    assert result.returncode == 3
    assert "pull token" in result.stderr


def test_a_token_response_without_a_token_fails_closed(run_script, registry):
    result = run_script(
        "check-tag-unpublished.sh", IMAGE, "3.1.0", env=registry(FAKE_TOKEN_BODY='{"errors":[]}')
    )

    assert result.returncode == 3
    assert "no token field" in result.stderr


def test_a_transport_failure_fails_closed(run_script, registry):
    result = run_script(
        "check-tag-unpublished.sh", IMAGE, "3.1.0", env=registry(FAKE_MANIFEST_EXIT="6")
    )

    assert result.returncode == 3


def test_a_github_token_is_used_for_the_pull_token_exchange(run_script, registry, tmp_path):
    log = tmp_path / "curl.log"
    result = run_script(
        "check-tag-unpublished.sh",
        IMAGE,
        "3.1.0",
        env=registry(
            FAKE_MANIFEST_STATUS="404", GITHUB_TOKEN="ghs-not-a-real-token", FAKE_CURL_LOG=str(log)
        ),
    )

    assert result.returncode == 0, result.output
    assert "--user x-access-token:ghs-not-a-real-token" in log.read_text()


def test_no_github_token_still_works_anonymously(run_script, registry, tmp_path):
    log = tmp_path / "curl.log"
    result = run_script(
        "check-tag-unpublished.sh",
        IMAGE,
        "3.1.0",
        env=registry(FAKE_MANIFEST_STATUS="404", FAKE_CURL_LOG=str(log)),
    )

    assert result.returncode == 0, result.output
    assert "--user" not in log.read_text()


def test_the_pull_token_is_scoped_to_pull_only(run_script, registry, tmp_path):
    log = tmp_path / "curl.log"
    run_script(
        "check-tag-unpublished.sh",
        IMAGE,
        "3.1.0",
        env=registry(FAKE_MANIFEST_STATUS="404", FAKE_CURL_LOG=str(log)),
    )

    assert "scope=repository:octoverse-id/octonomy:pull" in log.read_text()


def test_usage_errors(run_script, registry):
    env = registry(FAKE_DIGEST=DIGEST)

    assert run_script("check-tag-unpublished.sh", env=env).returncode == 2
    assert run_script("check-tag-unpublished.sh", IMAGE, env=env).returncode == 2
    assert run_script("check-tag-unpublished.sh", "octonomy", "3.1.0", env=env).returncode == 2
    assert run_script("check-tag-unpublished.sh", IMAGE, "3.1.0", "abc", env=env).returncode == 2
