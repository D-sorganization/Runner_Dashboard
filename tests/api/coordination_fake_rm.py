"""A fake Repository_Management checkout for the coordination API tests (#1229).

``FakeRM(tmp_path)`` writes tiny ``scripts/<name>.py`` modules that log their
argv to ``calls.log`` and print canned JSON from ``responses.json``. Tests set
responses with ``rm.respond(key, stdout=..., rc=..., stderr=...)`` where key is
``"<script>:<subcommand>"`` (``agent_communicate:list``) or ``"<script>"``.

The builders below (``claim_status``, ``lease_result``, ``board``) emit the
exact JSON shapes the real RM scripts print, so a test cannot pass against a
shape RM never produces. ``shared_scripts/agent_identity.py`` carries the
roster (``AGENT_IDS``) the API reads once via ``python -c``.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import identity
import pytest

SCRIPTS = ("agent_communicate", "check_agent_claim", "post_agent_lease", "release_agent_lease")
ROSTER = ("user", "orchestrator", "claude", "codex", "jules", "night-watch", "gemini")

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
        self.set_roster(ROSTER)
        self._spec: dict[str, Any] = {}
        self._save()

    def _save(self) -> None:
        (self.root / "responses.json").write_text(json.dumps(self._spec), encoding="utf-8")

    def respond(self, key: str, stdout: Any = None, rc: int = 0, stderr: str = "") -> None:
        self._spec[key] = {"stdout": stdout, "rc": rc, "stderr": stderr}
        self._save()

    def set_roster(self, agents: tuple[str, ...] | None) -> None:
        """Write RM ``shared_scripts/agent_identity.py``; ``None`` removes it (roster unavailable)."""
        pkg = self.root / "shared_scripts"
        pkg.mkdir(exist_ok=True)
        module = pkg / "agent_identity.py"
        if agents is None:
            module.unlink(missing_ok=True)
            return
        module.write_text(f"AGENT_IDS = {tuple(agents)!r}\n", encoding="utf-8")

    def disable_all_repos(self) -> None:
        self._spec["no_all_repos"] = True
        self._save()

    def calls(self) -> list[tuple[str, list[str]]]:
        log = self.root / "calls.log"
        if not log.exists():
            return []
        return [tuple(json.loads(line)) for line in log.read_text(encoding="utf-8").splitlines() if line]


def install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, principals: dict[str, identity.Principal], repos: str
) -> Iterator[FakeRM]:
    """Point the dashboard at a fresh ``FakeRM`` with ``principals`` keyed by bearer token; resets state around."""
    from coordination import board as board_mod  # noqa: PLC0415
    from coordination import claims as claims_mod  # noqa: PLC0415
    from coordination import roster as roster_mod  # noqa: PLC0415
    from staff import fleet as staff_fleet  # noqa: PLC0415
    from staff import runner as runner_mod  # noqa: PLC0415
    from staff import scheduler as scheduler_mod  # noqa: PLC0415
    from staff import store as store_mod  # noqa: PLC0415

    fake = FakeRM(tmp_path / "rm")
    monkeypatch.setenv("STAFF_RM_ROOT", str(fake.root))
    monkeypatch.setenv("STAFF_RM_PYTHON", sys.executable)
    monkeypatch.setenv("STAFF_RUNS_DB", str(tmp_path / "runs.sqlite3"))
    monkeypatch.setenv("STAFF_ROLES_DIR", str(tmp_path / "no-roles"))
    monkeypatch.setenv("STAFF_HOLDS_FILE", str(tmp_path / "holds.json"))
    monkeypatch.setenv("COORDINATION_REPOS", repos)
    monkeypatch.setenv("DASHBOARD_LOOPBACK_AUTH", "0")
    monkeypatch.delenv("HUB_FLEET_TOKEN", raising=False)
    monkeypatch.setattr(staff_fleet, "peer_nodes", lambda: {})
    monkeypatch.setattr(identity.identity_manager, "verify_token", lambda raw: principals.get(raw))
    resets = (
        store_mod.reset_store, runner_mod.reset_runner, scheduler_mod.reset_scheduler,
        board_mod.reset_cache, roster_mod.reset_cache, claims_mod.reset_locks,
    )  # fmt: skip
    for reset in resets:
        reset()
    yield fake
    for reset in reversed(resets):
        reset()


def bot(agent_id: str) -> identity.Principal:
    """A bot principal named by the ``agent-<name>`` convention."""
    return identity.Principal(id=agent_id, type="bot", name=agent_id, roles=["bot"])


def session(name: str, repo: str, issue: int = 7, agent: str = "claude") -> dict[str, Any]:
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


def claim_status(held: bool, agent: str | None = None, reason: str = "", expires_at: str | None = None) -> dict:
    """``check_agent_claim`` stdout: ``ClaimStatus`` fields; ``reason`` is ``error:<Exc>`` when it failed open."""
    return {"held": held, "agent": agent, "reason": reason, "expires_at": expires_at}


def lease_result(
    *, label: bool = True, comment: bool = True, errors: list[str] | None = None, agent: str = "codex"
) -> dict[str, Any]:
    """``post_agent_lease`` stdout: ``LeaseWriteResult`` + ``ok``/``agent``/``expires_at`` (errors is a list)."""
    errs = list(errors or [])
    return {
        "label_updated": label,
        "comment_posted": comment,
        "receipt": "https://github.com/D-sorganization/Tools/issues/12#issuecomment-1" if comment else "",
        "errors": errs,
        "ok": label and comment and not errs,
        "agent": agent,
        "expires_at": "2026-09-23T12:00:00+00:00",
    }


def release_result(*, label: bool = True, comment: bool = True, errors: list[str] | None = None) -> dict[str, Any]:
    """``release_agent_lease`` stdout (rc 1 when not ok): ``LeaseWriteResult`` + ``ok``."""
    out = lease_result(label=label, comment=comment, errors=errors)
    return {k: out[k] for k in ("label_updated", "comment_posted", "receipt", "errors", "ok")}
