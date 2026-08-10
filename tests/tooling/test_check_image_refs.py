from __future__ import annotations

import pytest

IMAGE = "ghcr.io/octoverse-id/octonomy"


@pytest.fixture
def config_file(tmp_path):
    """Write a fragment that looks like the files the real gate scans."""

    def _write(body: str, name: str = "deployment.yaml") -> str:
        path = tmp_path / name
        path.write_text(body)
        return str(path)

    return _write


def test_matching_version_passes(run_script, config_file):
    path = config_file(f"        image: {IMAGE}:3.1.0\n")

    result = run_script("check-image-refs.sh", "3.1.0", path)

    assert result.returncode == 0, result.output


def test_stale_version_fails(run_script, config_file):
    path = config_file(f"        image: {IMAGE}:3.0.1\n")

    result = run_script("check-image-refs.sh", "3.1.0", path)

    assert result.returncode == 1
    assert "stale" in result.stdout
    assert f"{IMAGE}:3.0.1" in result.stdout


def test_a_file_with_no_reference_fails(run_script, config_file):
    """The gate that matters. A grep finding nothing stays green forever, and once it
    does, nobody checks these references by hand any more."""

    path = config_file("        image: postgres:16\n")

    result = run_script("check-image-refs.sh", "3.1.0", path)

    assert result.returncode == 1
    assert "contains no" in result.stdout


def test_a_renamed_or_deleted_file_fails(run_script, tmp_path):
    result = run_script("check-image-refs.sh", "3.1.0", str(tmp_path / "moved.yaml"))

    assert result.returncode == 1
    assert "no such file" in result.stdout


def test_unrecognised_tag_fails(run_script, config_file):
    """`3.1.O` is a capital letter O. A permissive default would ship it."""

    path = config_file(f"        image: {IMAGE}:3.1.O\n")

    result = run_script("check-image-refs.sh", "3.1.0", path)

    assert result.returncode == 1
    assert "unrecognised tag" in result.stdout


@pytest.mark.parametrize("tag", ["latest", "edge"])
def test_documented_moving_tags_are_allowed_and_not_version_checked(run_script, config_file, tag):
    path = config_file(f"docker pull {IMAGE}:{tag}\n")

    result = run_script("check-image-refs.sh", "3.1.0", path)

    assert result.returncode == 0, result.output


def test_counts_are_not_asserted_only_presence(run_script, config_file):
    """compose.yaml keeps three literal references on purpose, and adding a service
    adds a fourth. Presence per file is the contract; an exact count is a gate that
    breaks on unrelated edits."""

    path = config_file(
        f"  api:\n    image: {IMAGE}:3.1.0\n"
        f"  migrate:\n    image: {IMAGE}:3.1.0\n"
        f"  dispatcher:\n    image: {IMAGE}:3.1.0\n"
        f"  worker:\n    image: {IMAGE}:3.1.0\n",
        name="compose.yaml",
    )

    result = run_script("check-image-refs.sh", "3.1.0", path)

    assert result.returncode == 0, result.output


def test_every_file_is_checked_not_just_the_first(run_script, config_file):
    good = config_file(f"        image: {IMAGE}:3.1.0\n", name="deployment.yaml")
    bad = config_file(f"        image: {IMAGE}:2.0.0\n", name="migrate-job.yaml")

    result = run_script("check-image-refs.sh", "3.1.0", good, bad)

    assert result.returncode == 1
    assert "migrate-job.yaml" in result.stdout


def test_all_problems_are_reported_not_just_the_first(run_script, config_file):
    first = config_file(f"        image: {IMAGE}:2.0.0\n", name="a.yaml")
    second = config_file(f"        image: {IMAGE}:3.1.O\n", name="b.yaml")

    result = run_script("check-image-refs.sh", "3.1.0", first, second)

    assert result.returncode == 1
    assert "a.yaml" in result.stdout
    assert "b.yaml" in result.stdout
    assert "2 problem(s)" in result.stderr


def test_a_database_url_is_not_an_image_reference(run_script, config_file):
    """`postgres://octonomy:PASSWORD@host` contains `octonomy:` — matching on the bare
    name instead of the full image would read the password as a tag."""

    path = config_file(
        '  DATABASE_URL: "postgres://octonomy:CHANGE_ME@db-host:5432/octonomy"\n'
        f"        image: {IMAGE}:3.1.0\n"
    )

    result = run_script("check-image-refs.sh", "3.1.0", path)

    assert result.returncode == 0, result.output


def test_markdown_fenced_references_are_matched(run_script, config_file):
    path = config_file(f"```bash\ndocker pull {IMAGE}:3.0.1\n```\n", name="deployment.md")

    result = run_script("check-image-refs.sh", "3.1.0", path)

    assert result.returncode == 1
    assert "stale" in result.stdout


def test_leading_v_on_the_version_is_accepted(run_script, config_file):
    path = config_file(f"        image: {IMAGE}:3.1.0\n")

    assert run_script("check-image-refs.sh", "v3.1.0", path).returncode == 0


def test_usage_errors(run_script, config_file):
    path = config_file(f"        image: {IMAGE}:3.1.0\n")

    assert run_script("check-image-refs.sh").returncode == 2
    assert run_script("check-image-refs.sh", "3.1.0").returncode == 2
    assert run_script("check-image-refs.sh", "not-a-version", path).returncode == 2
