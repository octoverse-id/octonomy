from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


@dataclass(frozen=True)
class Result:
    """The bits of a completed subprocess these tests assert on."""

    returncode: int
    stdout: str
    stderr: str

    @property
    def output(self) -> str:
        """stdout and stderr together — for asserting a message was surfaced at all."""

        return self.stdout + self.stderr


@pytest.fixture
def run_script():
    """Run one of scripts/*.sh and capture its outcome.

    The guards in scripts/ exist because they are the parts of the publish pipeline
    that fail *silently* when they are wrong, so the tests drive them the way the
    workflow does — as subprocesses, asserting on exit code and message, not by
    importing anything.
    """

    def _run(name: str, *args: str, env: dict[str, str] | None = None) -> Result:
        script = SCRIPTS / name
        assert script.exists(), f"{script} is missing"
        # A .py guard runs under THIS interpreter, not the shebang's `python`: the venv is
        # where its third-party imports live, and the Makefile invokes it the same way
        # (`uv run python`). Shell guards keep executing directly.
        launcher = [sys.executable] if script.suffix == ".py" else []
        completed = subprocess.run(
            [*launcher, str(script), *args],
            capture_output=True,
            text=True,
            env=env,
            timeout=60,
            check=False,
        )
        return Result(completed.returncode, completed.stdout, completed.stderr)

    return _run


@pytest.fixture
def scripts_dir() -> Path:
    return SCRIPTS


@pytest.fixture
def tag_list(tmp_path):
    """Write a git-tag listing to a file and return its path."""

    def _write(*tags: str) -> str:
        path = tmp_path / "tags.txt"
        path.write_text("\n".join(tags) + ("\n" if tags else ""))
        return str(path)

    return _write
