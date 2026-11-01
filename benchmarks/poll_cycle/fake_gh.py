"""Generates a fake ``gh`` CLI on disk so subprocess calls cost latency but no network."""

from __future__ import annotations

import stat
import sys
from pathlib import Path

_SCRIPT = '''#!{python}
"""Fake gh: sleeps like a network round-trip and answers from stub_github."""
import json, sys, time

sys.path.insert(0, {root!r})
from benchmarks.poll_cycle import stub_github  # noqa: E402

time.sleep({latency})
args = sys.argv[1:]
if args[:1] == ["api"]:
    ep = next((a for a in args[1:] if a.startswith(("/", "repos", "orgs"))), "/")
    print(json.dumps(stub_github.respond(ep if ep.startswith("/") else "/" + ep)[1]))
elif args[:2] == ["repo", "list"]:
    print(json.dumps([{{"name": f"repo{{i}}", "isArchived": False}} for i in range(stub_github.N_REPOS)]))
elif args[:2] == ["auth", "status"]:
    print("Logged in to github.com as stub")
else:
    print("[]")
'''


def install_fake_gh(bin_dir: Path, repo_root: Path, latency_s: float) -> Path:
    """Write an executable fake ``gh`` (it sleeps ``latency_s`` per call) and return its path."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    target = bin_dir / "gh"
    script = _SCRIPT.format(python=sys.executable, root=str(repo_root), latency=latency_s)
    target.write_text(script, encoding="utf-8")
    target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return target
