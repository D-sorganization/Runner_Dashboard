"""Tests for the publish allowlist and forbidden-path/pattern guard (issue #3080)."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "scripts"))
import publish_guard as pg  # noqa: E402

RULES = pg.load_rules()


def _check(tmp_path: Path, files: dict[str, str], rules: pg.Rules = RULES) -> list[pg.Finding]:
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text, encoding="utf-8")
    return pg.check(tmp_path, list(files), rules)


@pytest.mark.parametrize(
    ("rel", "want"),
    [
        ("backend/app.py", []),
        ("README.md", []),
        ("docs/security/NOTES-CONFIDENTIAL.md", ["forbidden-path:confidential-doc"]),
        ("deploy/tls/server.pem", ["forbidden-path:key-material"]),
        ("private-stuff/notes.md", ["outside-allowlist"]),
    ],
)
def test_paths(tmp_path: Path, rel: str, want: list[str]) -> None:
    assert [f.category for f in _check(tmp_path, {rel: "neutral\n"})] == want


@pytest.mark.parametrize("line", pg.CANARY_LINES)
def test_each_canary_line_fails_without_echo(tmp_path: Path, line: str) -> None:
    findings = _check(tmp_path, {"backend/leak.py": f"# {line}\n"})
    assert len(findings) == 1 and findings[0].line == 1
    assert line not in findings[0].render()


def test_self_test_passes_and_catches_a_dead_rule() -> None:
    assert pg.self_test(RULES)
    dead = pg.Rules(RULES.allow, RULES.path_globs, {**RULES.content, "github-token": re.compile("never-matches-xyz")})
    assert not pg.self_test(dead)


def test_overlay_literals_match_case_insensitively(tmp_path: Path) -> None:
    extra = tmp_path / "extra.json"
    extra.write_text(json.dumps(["PlaceholderHost"]), encoding="utf-8")
    findings = _check(tmp_path, {"docs/a.md": "runs on placeholderhost\n"}, pg.load_rules(extra=extra))
    assert [f.category for f in findings] == ["overlay-literal-0"]


def test_rule_files_hold_no_private_values() -> None:
    assert pg.check(_ROOT, ["config/publish/forbidden-patterns.json", "config/publish/publish-allowlist.txt"], RULES) == []


@pytest.mark.parametrize(("cli", "env", "want"), [(None, {}, "report"), ("enforce", {}, "enforce"), (None, {pg.MODE_ENV: "enforce"}, "enforce")])
def test_mode_is_a_single_flag(cli: str | None, env: dict[str, str], want: str) -> None:
    assert pg.resolve_mode(cli, env) == want
    with pytest.raises(SystemExit):
        pg.resolve_mode("lenient", {})


def test_report_exits_zero_and_enforce_exits_one(tmp_path: Path) -> None:
    _check(tmp_path, {"secret-dir/x.md": "neutral\n"})
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    assert pg.main(["--root", str(tmp_path), "--mode", "report"]) == 0
    assert pg.main(["--root", str(tmp_path), "--mode", "enforce"]) == 1
