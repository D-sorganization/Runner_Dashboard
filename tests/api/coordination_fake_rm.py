"""A fake Repository_Management checkout for the coordination API tests (#1229).

``FakeRM(tmp_path)`` writes tiny ``scripts/<name>.py`` modules that log their
argv to ``calls.log`` and print canned JSON from ``responses.json``. Tests set
responses with ``rm.respond(key, stdout=..., rc=..., stderr=...)`` where key is
``"<script>:<subcommand>"`` (``agent_communicate:list``) or ``"<script>"``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SCRIPTS = ("agent_communicate", "check_agent_claim", "post_agent_lease", "release_agent_lease")

_FAKE = """
import json, pathlib, sys
root = pathlib.Path(__file__).resolve().parents[1]
name = pathlib.Path(__file__).stem
argv = sys.argv[1:]
with open(root / "calls.log", "a", encoding="utf-8") as fh:
    fh.write(json.dumps([name, argv]) + "\\n")
spec = json.loads((root / "responses.json").read_text(encoding="utf-8"))
cmd = next((a for a in argv if a in ("register", "list", "inbox", "send", "ack", "release")), "")
if cmd == "list" and "--all-repos" in argv and spec.get("no_all_repos"):
    sys.stderr.write("error: unrecognized arguments: --all-repos\\n")
    sys.exit(2)
r = spec.get(name + ":" + cmd) or spec.get(name) or {}
sys.stderr.write(r.get("stderr", ""))
out = r.get("stdout")
if out is not None:
    sys.stdout.write(out if isinstance(out, str) else json.dumps(out))
    sys.stdout.write("\\n")
sys.exit(r.get("rc", 0))
"""


class FakeRM:
    def __init__(self, root: Path) -> None:
        self.root = root
        (root / "scripts").mkdir(parents=True, exist_ok=True)
        for name in SCRIPTS:
            (root / "scripts" / f"{name}.py").write_text(_FAKE, encoding="utf-8")
        self._spec: dict[str, Any] = {}
        self._save()

    def _save(self) -> None:
        (self.root / "responses.json").write_text(json.dumps(self._spec), encoding="utf-8")

    def respond(self, key: str, stdout: Any = None, rc: int = 0, stderr: str = "") -> None:
        self._spec[key] = {"stdout": stdout, "rc": rc, "stderr": stderr}
        self._save()

    def disable_all_repos(self) -> None:
        self._spec["no_all_repos"] = True
        self._save()

    def calls(self) -> list[tuple[str, list[str]]]:
        log = self.root / "calls.log"
        if not log.exists():
            return []
        return [tuple(json.loads(line)) for line in log.read_text(encoding="utf-8").splitlines() if line]


def session(name: str, repo: str, issue: int = 7, agent: str = "claude-code") -> dict[str, Any]:
    """One presence record shaped like RM ``replay`` output."""
    return {
        "kind": "presence",
        "id": f"evt-{name}",
        "session": name,
        "repo": repo,
        "agent": agent,
        "issue": issue,
        "branch": f"feat/{name}",
        "paths": ["backend/x.py"],
        "goals": {"api": "ship"},
        "at": "2026-09-22T10:00:00+00:00",
        "expires": "2026-09-22T12:00:00+00:00",
    }


def board(*sessions: dict[str, Any], complete: bool = True, warnings: list[str] | None = None) -> dict[str, Any]:
    return {
        "ok": complete and not warnings,
        "complete": complete,
        "sessions": list(sessions),
        "messages": [],
        "conflicts": [],
        "warnings": warnings or [],
    }
