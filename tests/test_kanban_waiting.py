"""Unit tests for Waiting-on-Dieter column assembly (#4321)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from kanban_waiting import build_waiting_on_dieter_column  # noqa: E402
from staff.holds import Hold, HoldsList  # noqa: E402

pytestmark = pytest.mark.unit

RD = "D-sorganization/Runner_Dashboard"


def _schedule_hold(hold_id: str, text: str) -> Hold:
    return Hold(id=hold_id, text=text, applies_to=["repo:Runner_Dashboard"], active=True, kind="schedule")


def test_build_waiting_column_unreadable_holds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "holds.json"
    path.write_text("{bad", encoding="utf-8")
    monkeypatch.setenv("COORDINATION_REPOS", "Runner_Dashboard")

    out = build_waiting_on_dieter_column(holds_list=HoldsList(path=path))
    assert out["column"] == "unknown"
    assert out["holds_readable"] is False
    assert out["items"] == []


def test_build_waiting_column_includes_hold_and_issue(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from coordination import claims as claims_mod  # noqa: PLC0415

    holds_path = tmp_path / "holds.json"
    HoldsList(path=holds_path).replace([_schedule_hold("rd-2657", "deploy hold").to_dict()])
    monkeypatch.setenv("COORDINATION_REPOS", "Runner_Dashboard")

    def fetch(path: str) -> Any:
        if path == f"/repos/{RD}/issues?state=open&per_page=100":
            return [
                {
                    "number": 77,
                    "state": "open",
                    "html_url": f"https://github.com/{RD}/issues/77",
                    "labels": [{"name": "hold:rd-2657"}],
                }
            ]
        return []

    monkeypatch.setattr(claims_mod, "evidence_fetch", fetch)

    out = build_waiting_on_dieter_column(repo="Runner_Dashboard", holds_list=HoldsList(path=holds_path))
    assert out["column"] == "waiting_on_dieter"
    kinds = {item["source_kind"] for item in out["items"]}
    assert kinds >= {"hold", "issue"}


def test_build_waiting_column_links_approval_for_qualified_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from coordination import claims as claims_mod  # noqa: PLC0415
    from staff.conversation_models import ActionProposalRecord  # noqa: PLC0415

    holds_path = tmp_path / "holds.json"
    HoldsList(path=holds_path).replace([_schedule_hold("rd-2657", "deploy hold").to_dict()])
    monkeypatch.setenv("COORDINATION_REPOS", "Runner_Dashboard")

    class _Store:
        def list_proposals(self, **_: Any) -> list[ActionProposalRecord]:
            return [
                ActionProposalRecord(
                    id="prop-1",
                    message_id="msg-1",
                    thread_id="thread-1",
                    action="staff.dispatch",
                    params={"repo": RD, "issue": 88},
                    state="proposed",
                )
            ]

    def fetch(path: str) -> Any:
        if path == f"/repos/{RD}/issues?state=open&per_page=100":
            return [
                {
                    "number": 88,
                    "state": "open",
                    "html_url": f"https://github.com/{RD}/issues/88",
                    "labels": [{"name": "hold:rd-2657"}],
                }
            ]
        return []

    monkeypatch.setattr(claims_mod, "evidence_fetch", fetch)

    out = build_waiting_on_dieter_column(
        repo="Runner_Dashboard",
        holds_list=HoldsList(path=holds_path),
        conversation_store=_Store(),  # type: ignore[arg-type]
    )
    label = "Runner_Dashboard#88"
    issue_item = next(i for i in out["items"] if i["source_kind"] == "issue" and i["source_label"] == label)
    assert issue_item["approval_url"] == "/staff?thread=thread-1"
