from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS = Path(__file__).parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import check_maxwell_contract_drift as drift  # noqa: E402


def test_snapshots_match_ignores_json_formatting() -> None:
    vendored = '{"info":{"title":"Maxwell"},"paths":{}}\n'
    upstream = '{\n  "paths": {},\n  "info": {"title": "Maxwell"}\n}'

    assert drift.snapshots_match(vendored, upstream) is True


def test_main_returns_zero_when_snapshots_match(tmp_path: Path, capsys) -> None:
    vendored = tmp_path / "vendored.json"
    upstream = tmp_path / "upstream.json"
    vendored.write_text('{"paths":{},"info":{"title":"Maxwell"}}\n', encoding="utf-8")
    upstream.write_text('{"info":{"title":"Maxwell"},"paths":{}}\n', encoding="utf-8")

    rc = drift.main(["--vendored", str(vendored), "--upstream-file", str(upstream)])

    assert rc == 0
    assert "matches upstream" in capsys.readouterr().out


def test_main_returns_one_on_drift_without_issue_token(tmp_path: Path, capsys, monkeypatch) -> None:
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    vendored = tmp_path / "vendored.json"
    upstream = tmp_path / "upstream.json"
    vendored.write_text('{"paths":{"/api/status":{}}}\n', encoding="utf-8")
    upstream.write_text('{"paths":{"/api/version":{}}}\n', encoding="utf-8")

    rc = drift.main(
        [
            "--vendored",
            str(vendored),
            "--upstream-file",
            str(upstream),
            "--issue-repo",
            "D-sorganization/Runner_Dashboard",
        ]
    )

    assert rc == 1
    stderr = capsys.readouterr().err
    assert "differs from upstream" in stderr
    assert "cannot record drift issue" in stderr
