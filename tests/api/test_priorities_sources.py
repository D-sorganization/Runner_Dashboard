"""Priorities sources and service: meetings, portfolios, degrade paths (issue #1227).

A throwaway Repository_Management tree is built under ``tmp_path`` and
``STAFF_RM_ROOT`` points at it, exactly how a dashboard node finds RM.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from priorities import service
from priorities.sources import SourceUnavailable, list_meetings, load_portfolios, read_meeting

FIXTURE = Path(__file__).parent / "fixtures" / "board_meeting" / "consensus.md.txt"

MANIFEST = """\
portfolios:
  work:
    description: Paid work.
    wip_limit: 12
    review_cadence: daily
    unattended_agents: [codex, claude]
    repos:
      - Gasification_Model
      - UpstreamDrift
  personal:
    description: Hobby.
    wip_limit: 2
    unattended_agents: []
    repos: [Games]
  broken: not-a-mapping
"""


def _rm_root(tmp_path: Path) -> Path:
    root = tmp_path / "Repository_Management"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "check_agent_claim.py").write_text("", encoding="utf-8")
    (root / "config").mkdir()
    (root / "config" / "fleet_manifest.yaml").write_text(MANIFEST, encoding="utf-8")
    (root / "docs" / "board-meetings").mkdir(parents=True)
    (root / "docs" / "board-meetings" / "README.md").write_text("readme", encoding="utf-8")
    return root


def _meeting(root: Path, date: str, *, consensus: bool = True, packet: str = "") -> Path:
    folder = root / "docs" / "board-meetings" / date
    folder.mkdir(parents=True)
    if consensus:
        shutil.copy(FIXTURE, folder / "consensus.md")
    if packet:
        (folder / "packet.md").write_text(packet, encoding="utf-8")
    return folder


@pytest.fixture
def rm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = _rm_root(tmp_path)
    monkeypatch.setenv("STAFF_RM_ROOT", str(root))
    monkeypatch.setenv("STAFF_DIRECTIVES_FILE", str(tmp_path / "directives.json"))
    return root


@pytest.mark.unit
def test_list_meetings_newest_first_ignores_non_dates(rm: Path) -> None:
    _meeting(rm, "2026-09-14", consensus=False, packet="# packet")
    _meeting(rm, "2026-09-21")
    (rm / "docs" / "board-meetings" / "not-a-date").mkdir()
    (rm / "docs" / "board-meetings" / "2026-13-40").mkdir()
    meetings = list_meetings(rm)
    assert [m["date"] for m in meetings] == ["2026-09-21", "2026-09-14"]
    assert meetings[0]["files"] == ["consensus.md"]
    assert meetings[0]["has_consensus"] is True
    assert meetings[1]["files"] == ["packet.md"]
    assert meetings[1]["has_consensus"] is False


@pytest.mark.unit
def test_read_meeting_returns_raw_and_parsed(rm: Path) -> None:
    _meeting(rm, "2026-09-21", packet="# the packet")
    meeting = read_meeting(rm, "2026-09-21")
    assert meeting is not None
    assert meeting["packet"] == "# the packet"
    assert meeting["instructions"] is None
    assert meeting["consensus"]["active"][0]["item"] == "Fleet coordination API"
    assert "Consensus Priority List" in meeting["consensus_markdown"]


@pytest.mark.unit
def test_read_meeting_unknown_or_invalid_date_is_none(rm: Path) -> None:
    assert read_meeting(rm, "2026-01-01") is None
    with pytest.raises(ValueError):
        read_meeting(rm, "../../etc")


@pytest.mark.unit
def test_portfolios_parsed_and_malformed_entries_skipped(rm: Path) -> None:
    portfolios = load_portfolios(rm)
    assert [p.name for p in portfolios] == ["work", "personal"]
    work = portfolios[0]
    assert work.wip_limit == 12
    assert work.unattended_agents == ["codex", "claude"]
    assert work.repos == ["Gasification_Model", "UpstreamDrift"]
    assert portfolios[1].review_cadence == ""


@pytest.mark.unit
def test_portfolios_missing_manifest_raises_unavailable(rm: Path) -> None:
    (rm / "config" / "fleet_manifest.yaml").unlink()
    with pytest.raises(SourceUnavailable):
        load_portfolios(rm)


@pytest.mark.unit
def test_snapshot_without_meetings_is_unavailable_but_keeps_portfolios(rm: Path) -> None:
    snap = service.priorities_snapshot()
    assert snap["available"] is False
    assert "no board meeting" in snap["reason"]
    assert snap["board"] is None
    assert [p["name"] for p in snap["portfolios"]] == ["work", "personal"]
    assert snap["generated_at"].endswith("Z")


@pytest.mark.unit
def test_snapshot_uses_latest_meeting_with_consensus(rm: Path) -> None:
    _meeting(rm, "2026-09-14")
    _meeting(rm, "2026-09-28", consensus=False, packet="# next week, not yet voted")
    snap = service.priorities_snapshot()
    assert snap["available"] is True
    assert snap["board"]["date"] == "2026-09-14"
    assert snap["board"]["active"][0]["rank"] == 1
    assert snap["board"]["deferred"][0]["reassess"] == "2026-10-05"
    assert snap["board"]["disagreements"]


@pytest.mark.unit
def test_snapshot_degrades_when_rm_root_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STAFF_RM_ROOT", str(tmp_path / "nowhere"))
    monkeypatch.setenv("STAFF_DIRECTIVES_FILE", str(tmp_path / "directives.json"))
    snap = service.priorities_snapshot()
    assert snap["available"] is False
    assert "Repository_Management" in snap["reason"]
    assert snap["portfolios"] == []
    assert snap["directives"] == []
    assert service.meetings_index()["available"] is False
    assert service.meeting_detail("2026-09-21")["available"] is False


@pytest.mark.unit
def test_top_priorities_board_then_directives(rm: Path) -> None:
    _meeting(rm, "2026-09-21")
    from priorities.directives import get_directives  # noqa: PLC0415

    get_directives().replace([{"text": "Finish the coordination API first", "priority": 1, "set_by": "operator"}])
    top = service.top_priorities(4)
    assert [t["kind"] for t in top] == ["board", "board", "board", "directive"]
    assert top[0]["item"] == "Fleet coordination API"
    assert top[3]["text"] == "Finish the coordination API first"
    assert service.top_priorities(2) == top[:2]
    assert service.top_priorities(0) == []


@pytest.mark.unit
def test_top_priorities_without_rm_returns_directives_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STAFF_RM_ROOT", str(tmp_path / "nowhere"))
    monkeypatch.setenv("STAFF_DIRECTIVES_FILE", str(tmp_path / "directives.json"))
    from priorities.directives import get_directives  # noqa: PLC0415

    get_directives().replace([{"text": "Hold merges on Tools", "priority": 2, "set_by": "operator"}])
    top = service.top_priorities(5)
    assert [t["kind"] for t in top] == ["directive"]
    with pytest.raises(ValueError):
        service.top_priorities(-1)
