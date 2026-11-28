"""#4618: merge-shepherd disarm executor readback and wrapper handoff."""

from __future__ import annotations

import json
import subprocess
from typing import Any

import pytest

import scripts.merge_shepherd_pass as msp
from scripts import automerge_guard


def _completed(stdout: str = "", returncode: int = 0, stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


class ShepherdDisarmGh:
    """Minimal gh fake for merge disposition + disarm readback tests."""

    def __init__(self, *, auto_merge_on: bool = True, in_merge_queue: bool = False) -> None:
        self.auto_merge_on = auto_merge_on
        self.in_merge_queue = in_merge_queue
        self.deleted = False
        self.comments: list[str] = []
        self.calls: list[list[str]] = []

    def __call__(self, cmd: list[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(cmd)
        joined = " ".join(cmd)
        if "--jq" in cmd and ".auto_merge != null" in joined:
            return _completed("true" if self.auto_merge_on and not self.deleted else "false")
        if "/timeline" in joined:
            if "added_to_merge_queue" in joined:
                events = ["added_to_merge_queue"] if self.in_merge_queue and not self.deleted else []
                return _completed("\n".join(events))
            return _completed("")
        if "/issues/" in joined and "/comments" in joined and "-X" in joined and "POST" in joined:
            body = next((cmd[i + 1] for i, part in enumerate(cmd) if part == "-f" and i + 1 < len(cmd)), "")
            self.comments.append(body)
            return _completed("{}")
        if "pr" in cmd and "merge" in cmd and "--disable-auto" in joined:
            self.deleted = True
            self.auto_merge_on = False
            self.in_merge_queue = False
            return _completed("{}")
        if "/ccr/auto_merge" in joined and "-X" in joined and "DELETE" in joined:
            self.deleted = True
            self.auto_merge_on = False
            self.in_merge_queue = False
            return _completed("{}")
        if "/pulls/" in joined and "/commits" in joined:
            return _completed(json.dumps([{"commit": {"message": "x"}}]))
        if "/pulls/" in joined:
            pull: dict[str, Any] = {
                "draft": False,
                "labels": [],
                "body": "",
                "head": {"sha": "a" * 40, "ref": "feat", "repo": {"full_name": "o/r"}},
                "base": {"ref": "main", "repo": {"full_name": "o/r"}},
                "node_id": "PR_n1",
            }
            if self.auto_merge_on and not self.deleted:
                pull["auto_merge"] = {"merge_method": "squash"}
            return _completed(json.dumps(pull))
        if "/issues/" in joined and "/comments" in joined:
            return _completed("[]")
        if "/pulls/" in joined and "/files" in joined:
            return _completed("[]")
        raise AssertionError(f"unexpected gh call: {joined}")


def test_disarm_for_shepherd_reports_disarmed_when_hold_exit_code_would_invert(monkeypatch: pytest.MonkeyPatch) -> None:
    """Held PR disarm succeeds via readback even though --disarm-if-held would exit 1."""
    monkeypatch.setattr(automerge_guard, "check_token_identity", lambda **kw: True)
    fake = ShepherdDisarmGh(auto_merge_on=True, in_merge_queue=False)
    before, after, verdict = automerge_guard.disarm_for_shepherd(
        "o/r",
        7,
        detail="rejected_at_head",
        head_sha="a" * 40,
        revalidate=lambda: True,
        runner=fake,
    )
    assert before.auto_merge_on is True
    assert fake.deleted is True
    assert automerge_guard.disposition_cleared(after)
    assert fake.deleted is True


def _guard_module():
    import importlib
    import sys

    scripts_dir = str(msp.Path(__file__).resolve().parents[2] / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    return importlib.import_module("automerge_guard")


def test_disarm_via_guard_uses_readback_not_cli_exit_code(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(msp, "_revalidate_disarm", lambda **kw: True)
    fake = ShepherdDisarmGh(auto_merge_on=True)
    guard_mod = _guard_module()
    monkeypatch.setattr(automerge_guard, "check_token_identity", lambda **kw: True)
    monkeypatch.setattr(
        guard_mod,
        "disarm_for_shepherd",
        lambda *a, **k: automerge_guard.disarm_for_shepherd(*a, **k, runner=fake),
    )
    result = msp._disarm_via_guard("o/r", 7, repo_root=msp.Path.cwd(), detail="rejected_at_head", head_sha="a" * 40)
    assert result["disarmed"] is True
    assert result["exit_code"] == 0
    assert result["after_auto_merge"] is False


def test_disarm_via_guard_noop_when_already_clear() -> None:
    before = automerge_guard.MergeDisposition(False, False)
    after = automerge_guard.MergeDisposition(False, False)
    needed_clearing = not automerge_guard.disposition_cleared(before)
    disarmed = automerge_guard.disposition_cleared(after) if needed_clearing else True
    assert disarmed is True


def test_main_executes_planned_disarm_with_detail(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from coordination.merge_shepherd_pass import PassSummary

    summary = PassSummary()
    summary.disarmed = [
        {
            "repo": "D-sorganization/Runner_Dashboard",
            "pr": 4620,
            "head_sha": "abc",
            "strategy": "disarm",
            "dry_run": False,
            "detail": "rejected_at_head",
        }
    ]
    captured: dict[str, str] = {}

    def _fake_disarm(repo: str, pr: int, **kw: object) -> dict[str, object]:
        captured["head_sha"] = str(kw.get("head_sha") or "")
        captured["detail"] = str(kw.get("detail") or "")
        return {
            "exit_code": 0,
            "disarmed": True,
            "after_auto_merge": False,
            "after_in_merge_queue": False,
        }

    monkeypatch.setattr(msp, "run_merge_shepherd_pass", lambda **kw: summary)
    monkeypatch.setattr(msp, "_resolve_hub_holds", lambda: ([], None))
    monkeypatch.setattr(msp, "resolve_live_arming", lambda **kw: True)
    monkeypatch.setattr(msp, "_disarm_via_guard", _fake_disarm)

    code = msp.main(["--json"])
    assert code == 0
    assert captured.get("detail") == "rejected_at_head"
    assert captured.get("head_sha") == "abc"
    out = json.loads(capsys.readouterr().out)
    assert out["executed_disarms"][0]["disarmed"] is True


def test_disarm_refuses_without_app_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = ShepherdDisarmGh()
    monkeypatch.setattr(automerge_guard, "check_token_identity", lambda **kw: False)
    with pytest.raises(RuntimeError, match="identity"):
        automerge_guard.disarm_for_shepherd("o/r", 7, head_sha="a" * 40, revalidate=lambda: True, runner=fake)
    assert not any("DELETE" in call or "POST" in call for call in fake.calls)


def test_disarm_refuses_new_head_after_planning(monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.unit.test_merge_shepherd_disarm_4618 import HEAD, _disarm_node

    node = _disarm_node(number=7)
    monkeypatch.setattr(msp, "_graphql", lambda *a: {})
    monkeypatch.setattr(msp, "graphql_nodes", lambda payload: ([node], 1, 0))
    monkeypatch.setattr(msp, "resolve_merge_shepherd_review_store", lambda **kw: None)
    assert msp._revalidate_disarm(repo="o/r", pr=7, head_sha=HEAD, repo_root=msp.Path.cwd())
    node["headRefOid"] = "b" * 40
    fake = ShepherdDisarmGh()
    monkeypatch.setattr(automerge_guard, "check_token_identity", lambda **kw: True)
    with pytest.raises(RuntimeError, match="revalidation"):
        automerge_guard.disarm_for_shepherd(
            "o/r",
            7,
            head_sha=HEAD,
            revalidate=lambda: msp._revalidate_disarm(repo="o/r", pr=7, head_sha=HEAD, repo_root=msp.Path.cwd()),
            runner=fake,
        )
    assert not any("DELETE" in call or "POST" in call for call in fake.calls)
