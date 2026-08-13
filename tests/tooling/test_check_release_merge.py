from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

PYPROJECT = '[project]\nname = "octonomy"\nversion = "{version}"\n'


@dataclass(frozen=True)
class Repo:
    """A throwaway git repo plus a way to add commits that move the version."""

    path: Path
    commit: Callable[[str | None, str], str]

    def __str__(self) -> str:  # so tests can pass it straight to the script
        return str(self.path)


def _git(repo: Path, *args: str) -> str:
    """Run git in `repo`, with identity forced so commits work on a bare CI runner."""

    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "HOME": str(repo),
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.com",
        },
    )
    return completed.stdout.strip()


@pytest.fixture
def repo(tmp_path):
    """A git repo whose pyproject version can be moved commit by commit.

    These tests build real repositories rather than stubbing git out, because the
    behaviour most likely to be wrong is which *parent* the script compares against —
    and a merge commit is the only way to exercise that.
    """
    path = tmp_path / "repo"
    path.mkdir()
    _git(path, "init", "-q", "-b", "main")

    def commit(version: str | None, message: str) -> str:
        if version is None:
            (path / "README.md").write_text(message)
        else:
            (path / "pyproject.toml").write_text(PYPROJECT.format(version=version))
        _git(path, "add", "-A")
        _git(path, "commit", "-q", "--allow-empty", "-m", message)
        return _git(path, "rev-parse", "HEAD")

    return Repo(path=path, commit=commit)


def test_the_release_merge_passes(run_script, repo):
    repo.commit("3.0.1", "before")
    release = repo.commit("3.1.0", "bump to 3.1.0")

    result = run_script("check-release-merge.sh", "3.1.0", release, str(repo))

    assert result.returncode == 0, result.output
    assert "is the release merge" in result.stdout


def test_a_later_commit_carrying_the_same_version_is_refused(run_script, repo):
    """The bug this script exists for. Only release PRs bump the version, so every
    commit merged afterwards still matches — and every other publish gate passes."""

    repo.commit("3.0.1", "before")
    repo.commit("3.1.0", "bump to 3.1.0")
    later = repo.commit(None, "docs: unrelated change merged after the release")

    result = run_script("check-release-merge.sh", "3.1.0", later, str(repo))

    assert result.returncode == 1
    assert "is not the release merge" in result.stdout
    assert "already carries 3.1.0" in result.stdout


def test_a_merge_commit_is_judged_by_its_first_parent(run_script, repo):
    """The ^1 case, and the reason these tests build real repos.

    On a release merge the second parent is the release branch, which already carries
    the bump. Comparing against ^2 would refuse every genuine release.
    """

    base = repo.commit("3.0.1", "before")
    _git(repo.path, "checkout", "-q", "-b", "release/3.1.0")
    repo.commit("3.1.0", "bump to 3.1.0")
    _git(repo.path, "checkout", "-q", "main")
    _git(repo.path, "merge", "-q", "--no-ff", "release/3.1.0", "-m", "Merge release/3.1.0")
    merge = _git(repo.path, "rev-parse", "HEAD")

    assert _git(repo.path, "rev-parse", f"{merge}^1") == base, "first parent should be main"

    result = run_script("check-release-merge.sh", "3.1.0", merge, str(repo))

    assert result.returncode == 0, result.output
    assert "first parent had 3.0.1" in result.stdout


def test_tag_version_disagreeing_with_the_tree_is_refused(run_script, repo):
    repo.commit("3.0.1", "before")
    head = repo.commit("3.1.0", "bump to 3.1.0")

    result = run_script("check-release-merge.sh", "3.2.0", head, str(repo))

    assert result.returncode == 1
    assert "carries version 3.1.0, not 3.2.0" in result.stdout


def test_a_root_commit_is_allowed(run_script, repo):
    """Nothing precedes it, so it cannot be a later commit that inherited the version."""

    root = repo.commit("0.1.0", "initial")

    result = run_script("check-release-merge.sh", "0.1.0", root, str(repo))

    assert result.returncode == 0, result.output
    assert "root commit" in result.stdout


def test_a_commit_introducing_pyproject_is_allowed(run_script, repo):
    repo.commit(None, "no pyproject yet")
    introduced = repo.commit("0.1.0", "add pyproject")

    result = run_script("check-release-merge.sh", "0.1.0", introduced, str(repo))

    assert result.returncode == 0, result.output
    assert "introduced pyproject.toml" in result.stdout


def test_an_annotated_tag_is_dereferenced_to_its_commit(run_script, repo):
    """publish-image.yml passes a SHA, but a maintainer running this pre-push will
    reach for the tag name."""

    repo.commit("3.0.1", "before")
    repo.commit("3.1.0", "bump to 3.1.0")
    _git(repo.path, "tag", "-a", "v3.1.0", "-m", "Octonomy 3.1.0")

    result = run_script("check-release-merge.sh", "3.1.0", "v3.1.0", str(repo))

    assert result.returncode == 0, result.output


def test_leading_v_on_the_version_is_accepted(run_script, repo):
    repo.commit("3.0.1", "before")
    release = repo.commit("3.1.0", "bump to 3.1.0")

    assert run_script("check-release-merge.sh", "v3.1.0", release, str(repo)).returncode == 0


def test_an_unknown_commit_fails_closed_rather_than_passing(run_script, repo):
    """Exit 3, not 0. A version this script cannot reason about must never be published
    on the strength of its silence."""

    repo.commit("3.1.0", "only commit")

    result = run_script("check-release-merge.sh", "3.1.0", "deadbeef", str(repo))

    assert result.returncode == 3
    assert "not a commit" in result.stderr


def test_usage_errors(run_script, repo):
    assert run_script("check-release-merge.sh").returncode == 2
    assert run_script("check-release-merge.sh", "not-a-version").returncode == 2
    assert run_script("check-release-merge.sh", "3.1.0", "HEAD", "/nope/nope").returncode == 2
