from __future__ import annotations

RELEASED = ("v1.0.0", "v2.0.0", "v3.0.0", "v3.0.1")


def tags(result) -> list[str]:
    return result.stdout.split()


def test_newest_release_gets_the_full_tag_set(run_script, tag_list):
    result = run_script("resolve-latest-tag.sh", "3.1.0", tag_list(*RELEASED))

    assert result.returncode == 0, result.output
    assert tags(result) == ["3.1.0", "3.1", "latest"]


def test_backport_after_a_newer_release_does_not_move_latest(run_script, tag_list):
    """The regression this script exists for.

    metadata-action's `latest=auto` would tag a v2.0.2 backport `:latest` because it
    only looks at the tag event, never at the other tags. That silently downgrades
    everyone running imagePullPolicy: Always from 3.1.0 to 2.0.2.
    """

    result = run_script("resolve-latest-tag.sh", "2.0.2", tag_list(*RELEASED, "v3.1.0"))

    assert result.returncode == 0, result.output
    assert tags(result) == ["2.0.2", "2.0"]
    assert "latest" not in tags(result)


def test_the_minor_tag_does_not_move_backward_either(run_script, tag_list):
    """`:3.1` points at the newest 3.1.x, so re-promoting 3.1.0 after 3.1.1 shipped
    would move it backward exactly as wrongly as moving `:latest` would. Reachable
    through the supported recovery path, where re-running an already-published version
    re-promotes its digest."""

    result = run_script("resolve-latest-tag.sh", "3.1.0", tag_list(*RELEASED, "v3.1.0", "v3.1.1"))

    assert result.returncode == 0, result.output
    assert tags(result) == ["3.1.0"]


def test_a_backport_keeps_its_own_minor_tag_when_it_is_the_newest_patch(run_script, tag_list):
    """2.0.2 is the newest 2.0.x, so it owns `:2.0` even though 3.1.0 owns `:latest`."""

    result = run_script("resolve-latest-tag.sh", "2.0.2", tag_list("v2.0.0", "v2.0.1", "v3.1.0"))

    assert result.returncode == 0, result.output
    assert tags(result) == ["2.0.2", "2.0"]


def test_a_superseded_backport_moves_no_tag_but_its_own(run_script, tag_list):
    result = run_script("resolve-latest-tag.sh", "2.0.2", tag_list("v2.0.3", "v3.1.0"))

    assert result.returncode == 0, result.output
    assert tags(result) == ["2.0.2"]


def test_a_newer_patch_on_a_different_line_does_not_hold_back_the_minor_tag(run_script, tag_list):
    """v3.2.1 is greater than 3.1.0 and suppresses `:latest`, but it is not in the 3.1
    line, so it has no claim on `:3.1`."""

    result = run_script("resolve-latest-tag.sh", "3.1.0", tag_list("v3.1.0", "v3.2.1"))

    assert result.returncode == 0, result.output
    assert tags(result) == ["3.1.0", "3.1"]


def test_patch_on_the_current_line_still_moves_latest(run_script, tag_list):
    result = run_script("resolve-latest-tag.sh", "3.0.2", tag_list(*RELEASED))

    assert result.returncode == 0, result.output
    assert tags(result) == ["3.0.2", "3.0", "latest"]


def test_the_version_being_published_may_already_be_tagged(run_script, tag_list):
    """The normal case: the workflow is triggered *by* the tag push, so `git tag
    --list` already contains it. It must not count as a newer release than itself."""

    result = run_script("resolve-latest-tag.sh", "v3.1.0", tag_list(*RELEASED, "v3.1.0"))

    assert result.returncode == 0, result.output
    assert tags(result) == ["3.1.0", "3.1", "latest"]


def test_leading_v_is_optional(run_script, tag_list):
    with_v = run_script("resolve-latest-tag.sh", "v3.1.0", tag_list(*RELEASED))
    without_v = run_script("resolve-latest-tag.sh", "3.1.0", tag_list(*RELEASED))

    assert tags(with_v) == tags(without_v)


def test_major_bump_compares_numerically_not_lexically(run_script, tag_list):
    """String comparison puts "9.0.0" above "10.0.0"."""

    result = run_script("resolve-latest-tag.sh", "10.0.0", tag_list("v9.0.0", "v9.12.0"))

    assert result.returncode == 0, result.output
    assert tags(result) == ["10.0.0", "10.0", "latest"]


def test_minor_bump_compares_numerically_not_lexically(run_script, tag_list):
    result = run_script("resolve-latest-tag.sh", "3.9.0", tag_list("v3.10.0"))

    assert result.returncode == 0, result.output
    assert tags(result) == ["3.9.0", "3.9"]


def test_non_release_tags_are_ignored(run_script, tag_list):
    """`v3.1`, a bare branch-shaped name and a prerelease are not releases, so none of
    them may suppress `:latest` — nor may they be treated as the newest version."""

    result = run_script(
        "resolve-latest-tag.sh",
        "3.1.0",
        tag_list("v3.1", "release-candidate", "v9.9.9-rc1", "v3.0.1"),
    )

    assert result.returncode == 0, result.output
    assert tags(result) == ["3.1.0", "3.1", "latest"]


def test_empty_tag_list_fails_closed(run_script, tag_list):
    """A shallow checkout enumerates no tags, so every release would look newest and
    take `:latest`. Exit rather than hand out the tag this script guards."""

    result = run_script("resolve-latest-tag.sh", "2.0.2", tag_list())

    assert result.returncode == 3
    assert "fetch-depth" in result.stderr


def test_tag_list_of_only_non_releases_fails_closed(run_script, tag_list):
    result = run_script("resolve-latest-tag.sh", "2.0.2", tag_list("main", "v3.1"))

    assert result.returncode == 3
    assert "fetch-depth" in result.stderr


def test_missing_tag_list_file_is_a_usage_error(run_script, tmp_path):
    result = run_script("resolve-latest-tag.sh", "3.1.0", str(tmp_path / "nope.txt"))

    assert result.returncode == 2


def test_no_arguments_is_a_usage_error(run_script):
    assert run_script("resolve-latest-tag.sh").returncode == 2


def test_malformed_version_is_rejected(run_script, tag_list):
    for bad in ("3.1", "3.1.0.1", "v3.1.O", "latest", "3.1.0-rc1", ""):
        result = run_script("resolve-latest-tag.sh", bad, tag_list(*RELEASED))
        assert result.returncode == 2, f"{bad!r} was accepted: {result.output}"


def test_the_script_carries_no_prerelease_rules(scripts_dir):
    """The trigger glob is `v[0-9]+.[0-9]+.[0-9]+`, so a prerelease tag cannot reach
    this script. Rules for it would be code no run ever executes — the review process
    specified them into existence once already. This is what fails if they come back."""

    source = (scripts_dir / "resolve-latest-tag.sh").read_text().lower()
    code = "\n".join(line for line in source.splitlines() if not line.lstrip().startswith("#"))

    assert "prerelease" not in code
    assert "pre-release" not in code
    assert "alpha" not in code
    assert "-rc" not in code
