"""Freshness failures cannot claim a current index or trigger Git transport."""

import subprocess
from pathlib import Path

import pytest
from staff.codebase_freshness import snapshot_is_stale

pytestmark = pytest.mark.unit


def test_timeout_is_stale_and_git_environment_is_isolated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIT_DIR", "/unrelated/repository")
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        assert "GIT_DIR" not in kwargs["env"] and "GIT_CONFIG_COUNT" not in kwargs["env"]
        assert kwargs["env"]["GIT_ALLOW_PROTOCOL"] == ""
        assert kwargs["timeout"] == 2
        raise subprocess.TimeoutExpired(args, 2)

    monkeypatch.setattr(subprocess, "run", run)
    assert snapshot_is_stale(Path("/checkout"), "a" * 40)
    assert len(calls) == 1 and calls[0][0][3:5] == ["rev-parse", "--verify"]


@pytest.mark.parametrize("indexed", [None, "", "a" * 8, "z" * 40])
def test_unknown_index_identity_is_stale(indexed: str | None) -> None:
    assert snapshot_is_stale(Path("/missing"), indexed)
