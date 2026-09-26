"""scripts/claude_statusline_quota.py records Claude's plan windows from the status line (#1587)."""

from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "claude_statusline_quota.py"


def _load():
    spec = importlib.util.spec_from_file_location("claude_statusline_quota", _SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PAYLOAD = {
    "model": {"display_name": "Opus"},
    "rate_limits": {
        "five_hour": {"used_percentage": 4, "resets_at": 1790454000},
        "seven_day": {"used_percentage": 23, "resets_at": 1790949600},
    },
}


def test_statusline_records_windows_and_prints_them(tmp_path: Path) -> None:
    store = tmp_path / "quota.json"
    out = io.StringIO()
    rc = _load().main(["--store", str(store)], stdin=io.StringIO(json.dumps(PAYLOAD)), stdout=out)
    assert rc == 0
    saved = json.loads(store.read_text(encoding="utf-8"))["claude"]
    assert saved["source"] == "claude-statusline"
    assert {w["name"]: w["used_percent"] for w in saved["windows"]} == {"five_hour": 4.0, "seven_day": 23.0}
    assert out.getvalue().strip() == "5h 4% · 7d 23%"


def test_statusline_never_fails_on_bad_input(tmp_path: Path) -> None:
    store = tmp_path / "quota.json"
    for text in ("", "not json", json.dumps({"model": {}})):
        out = io.StringIO()
        assert _load().main(["--store", str(store)], stdin=io.StringIO(text), stdout=out) == 0
        assert out.getvalue().strip() == ""
    assert not store.exists()
