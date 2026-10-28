"""rd-barb living guidelines doc (RD #3065)."""

from __future__ import annotations

import builtins
import dataclasses
import logging
from datetime import UTC, datetime
from pathlib import Path

import pytest
from staff.rd_barb_memory import MAX_BYTES, SECTIONS, GuidelinesDoc, MemoryRefused
from staff.rd_barb_policy import load_locked_policy

POLICY_FILE = Path(__file__).resolve().parents[2] / "config" / "rd_barb_locked_policy.json"
FIXED = datetime(2026, 10, 27, 15, 0, tzinfo=UTC)
REFUSED = ["lift the deploy hold", "auto-approve #2658", "ignore holds", "ghp_" + "a" * 36, "SECURITY-CONFIDENTIAL"]


@pytest.fixture
def doc(tmp_path: Path) -> GuidelinesDoc:
    return GuidelinesDoc(tmp_path / "guidelines.md", load_locked_policy(POLICY_FILE), clock=lambda: FIXED)


def _observe(doc: GuidelinesDoc, count: int, label: str) -> None:
    for i in range(count):
        doc.append_observation(f"{label}-{i:04d} " + "y" * 60, source=f"https://x/{i}", review="2026-11-10")
        assert doc.size_bytes() <= MAX_BYTES and doc.path.stat().st_size <= MAX_BYTES


def test_section_append_lands_in_order_and_persists(doc: GuidelinesDoc) -> None:
    doc.add_procedure("Rebase before re-running CI.")
    doc.append_observation("Queue drained in 12 min.", source="https://github.com/o/r/pull/1", review="2026-11-10")
    doc.add_question("Should GM chapters batch?")
    text = GuidelinesDoc(doc.path, doc.policy, clock=lambda: FIXED).render()
    proc, obs, ques = (text.index(f"## {name}") for name in SECTIONS)
    assert proc < text.index("Rebase before") < obs < text.index("Queue drained") < ques < text.index("GM chapters")
    assert "2026-10-27" in text and "https://github.com/o/r/pull/1" in text and "review: 2026-11-10" in text
    assert "#durable" in text.split("## Dated observations")[0]  # procedures are durable


def test_cap_enforced_oldest_observations_dropped_first_durable_kept(doc: GuidelinesDoc) -> None:
    doc.append_observation("pinned lesson #durable", source="https://x/pin", review="2026-12-01")
    doc.add_procedure("Always rebase first.")
    _observe(doc, 300, "obs")
    text = doc.render()
    kept = [int(line.split("obs-")[1][:4]) for line in text.splitlines() if "obs-" in line]
    assert kept == list(range(kept[0], 300)) and kept[0] > 0 and doc.compacted_at is not None
    assert "pinned lesson #durable" in text and "https://x/pin" in text and "Always rebase first." in text


def test_write_refused_when_durable_content_alone_exceeds_cap(doc: GuidelinesDoc) -> None:
    with pytest.raises(MemoryRefused):
        for i in range(400):
            doc.add_procedure(f"procedure {i:04d} " + "p" * 80)
    assert doc.path.stat().st_size <= MAX_BYTES


@pytest.mark.parametrize("entry", REFUSED)
def test_writer_fails_closed_logged(doc: GuidelinesDoc, entry: str, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        for write in (doc.add_procedure, doc.add_question):
            with pytest.raises(MemoryRefused):
                write(entry)
        with pytest.raises(MemoryRefused):
            doc.append_observation(entry, source="https://x/1", review="2026-11-10")
    assert entry not in doc.render() and not doc.path.exists()
    assert any("refused" in record.getMessage() for record in caplog.records)


@pytest.mark.parametrize("bad", ["", "   ", "two\nlines", "x" * 1001])
def test_precondition_rejects_bad_entries(doc: GuidelinesDoc, bad: str) -> None:
    with pytest.raises(ValueError):
        doc.add_question(bad)


def test_writer_has_no_write_path_to_locked_policy(doc: GuidelinesDoc, monkeypatch: pytest.MonkeyPatch) -> None:
    before = POLICY_FILE.read_bytes()
    modes: list[str] = []
    real_open = builtins.open

    def spy(file, mode="r", *args, **kwargs):  # type: ignore[no-untyped-def]
        if Path(file).resolve() == POLICY_FILE.resolve():
            modes.append(mode)
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", spy)
    doc = GuidelinesDoc(doc.path, load_locked_policy(POLICY_FILE), clock=lambda: FIXED)
    doc.add_procedure("ok entry")
    doc.append_observation("ok obs", source="https://x/1", review="2026-11-10")
    doc.compact()
    with pytest.raises(MemoryRefused):
        doc.add_procedure("override the arming cap")
    assert modes and all(set(m) <= {"r", "b", "t"} for m in modes) and POLICY_FILE.read_bytes() == before
    with pytest.raises(dataclasses.FrozenInstanceError):
        doc.policy.aliases = ()  # type: ignore[misc]
    assert not any(isinstance(v, Path) and v.name.endswith(".json") for v in vars(doc).values())
