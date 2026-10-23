"""scripts/import_dispatch_exclusions.py parses the orchestrator's DO_NOT_DISPATCH.txt format."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts import import_dispatch_exclusions as imp

SAMPLE = """# "<Repo> <issue>  # owner/reason" - dispatch.sh refuses these (peer-session lanes).
UpstreamDrift 13112  # OpenSim Work: FD replay chaos, needs private capture fits
UpstreamDrift 13157  # OpenSim Work lane

Tools *  # peer-codex: whole repo refactor
"""


@pytest.mark.unit
def test_parse_sample_format() -> None:
    assert imp.parse_text(SAMPLE) == [
        {
            "repo": "UpstreamDrift",
            "issue": 13112,
            "owner": "OpenSim Work",
            "reason": "OpenSim Work: FD replay chaos, needs private capture fits",
        },
        {"repo": "UpstreamDrift", "issue": 13157, "owner": imp.DEFAULT_OWNER, "reason": "OpenSim Work lane"},
        {"repo": "Tools", "issue": None, "owner": "peer-codex", "reason": "peer-codex: whole repo refactor"},
    ]


@pytest.mark.unit
@pytest.mark.parametrize("line", ["OnlyRepo", "Repo abc # r", "Repo 0 # r", "Repo 5", "Repo 5 extra # r"])
def test_malformed_lines_raise(line: str) -> None:
    with pytest.raises(ValueError):
        imp.parse_line(line)


@pytest.mark.unit
def test_dry_run_prints_and_sends_nothing(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    f = tmp_path / "dnd.txt"
    f.write_text(SAMPLE, encoding="utf-8")
    monkeypatch.setattr(imp, "_post", lambda *a, **k: pytest.fail("dry run must not POST"))
    with caplog.at_level("INFO", logger="import_dispatch_exclusions"):
        assert imp.main([str(f)]) == 0
    assert caplog.text.count("DRY-RUN POST") == 3
