"""Pre-commit hooks must match what CI enforces (no local-only drift).

RD main failed its own pre-commit hooks: the ruff hooks linted/formatted every
Python file while CI Standard only checks ``backend/`` and ``clients/``, and
detect-secrets rewrote ``.secrets.baseline`` with backslash paths on Windows.
Both blocked local pushes of rebased branches.
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path
from typing import Any

import yaml

from scripts import detect_secrets_stable as dss

REPO_ROOT = Path(__file__).resolve().parent.parent
CI_STANDARD = REPO_ROOT / ".github" / "workflows" / "ci-standard.yml"


def _precommit() -> dict[str, Any]:
    return yaml.safe_load((REPO_ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8"))


def _ruff_repo() -> dict[str, Any]:
    return next(r for r in _precommit()["repos"] if r["repo"].endswith("astral-sh/ruff-pre-commit"))


def _precommit_ruff_version() -> str:
    return str(_ruff_repo()["rev"]).lstrip("v")


def _uv_lock_ruff_version() -> str:
    lock = tomllib.loads((REPO_ROOT / "uv.lock").read_text(encoding="utf-8"))
    return next(p["version"] for p in lock["package"] if p["name"] == "ruff")


def _ci_lint_paths() -> list[str]:
    text = CI_STANDARD.read_text(encoding="utf-8")
    match = re.search(r"run:\s*ruff check (?P<paths>[^\n]+)", text)
    assert match, "CI Standard no longer runs `ruff check <paths>`"
    return sorted(p.rstrip("/") for p in match.group("paths").split())


def test_precommit_ruff_rev_matches_ci_lockfile() -> None:
    """CI installs ruff via `uv sync --frozen`, so uv.lock is CI's ruff."""
    assert _precommit_ruff_version() == _uv_lock_ruff_version()


def test_precommit_ruff_rev_matches_ci_fallback_pins() -> None:
    text = CI_STANDARD.read_text(encoding="utf-8")
    version = _precommit_ruff_version()
    assert re.findall(r"ruff==([0-9][0-9A-Za-z.\-]*)", text) == [version]
    assert f'grep -q "rev: v{version}"' in text


def test_ruff_hooks_cover_exactly_ci_lint_scope() -> None:
    hooks = {h["id"]: h for h in _ruff_repo()["hooks"]}
    # ruff discovers only *.py/*.pyi under the CI paths, never extensionless scripts.
    expected = "^(" + "|".join(_ci_lint_paths()) + r")/.*\.pyi?$"
    for hook_id in ("ruff", "ruff-format"):
        assert hooks[hook_id].get("files") == expected, f"{hook_id} must lint only CI's paths"


def test_detect_secrets_hook_uses_path_stable_wrapper() -> None:
    repo = next(r for r in _precommit()["repos"] if r["repo"].endswith("Yelp/detect-secrets"))
    hook = next(h for h in repo["hooks"] if h["id"] == "detect-secrets")
    assert hook["entry"] == "python scripts/detect_secrets_stable.py"
    assert re.fullmatch(r"[0-9a-f]{40}", repo["rev"]), "detect-secrets stays SHA-pinned"


def _baseline(results: dict[str, list[dict[str, Any]]], stamp: str = "2026-10-22T23:53:58Z") -> dict[str, Any]:
    return {"version": "1.5.0", "results": results, "generated_at": stamp}


def _finding(filename: str, line: int = 3) -> dict[str, Any]:
    return {"type": "Hex High Entropy String", "filename": filename, "hashed_secret": "x", "line_number": line}


def test_windows_rewrite_with_only_separator_churn_is_a_no_op() -> None:
    before = _baseline({"a/b.json": [_finding("a/b.json")]})
    after = _baseline({"a\\b.json": [_finding("a\\b.json")]}, stamp="2026-10-27T12:00:00Z")
    assert dss.reconcile(before, after) is None


def test_genuine_change_is_kept_with_posix_paths_in_original_order() -> None:
    before = _baseline({"z/y.py": [_finding("z/y.py")], "a/b.json": [_finding("a/b.json")]})
    after = _baseline({"a\\b.json": [_finding("a\\b.json", 9)], "z\\y.py": [_finding("z\\y.py")]})
    fixed = dss.reconcile(before, after)
    assert fixed is not None
    assert list(fixed["results"]) == ["z/y.py", "a/b.json"]
    assert fixed["results"]["a/b.json"][0] == _finding("a/b.json", 9)


def test_settle_baseline_restores_original_bytes(tmp_path: Path) -> None:
    original = (json.dumps(_baseline({"a/b.json": [_finding("a/b.json")]}), indent=2) + "\n").encode()
    path = tmp_path / ".secrets.baseline"
    rewritten = _baseline({"a\\b.json": [_finding("a\\b.json")]}, stamp="2026-10-27T12:00:00Z")
    path.write_text(json.dumps(rewritten, indent=2).replace("\n", "\r\n"), encoding="utf-8")
    assert dss.settle_baseline(path, original) == 0
    assert path.read_bytes() == original


def test_settle_baseline_reports_real_update_with_lf(tmp_path: Path) -> None:
    original = (json.dumps(_baseline({"a/b.json": [_finding("a/b.json")]}), indent=2) + "\n").encode()
    path = tmp_path / ".secrets.baseline"
    path.write_text(json.dumps(_baseline({"a\\b.json": [_finding("a\\b.json", 7)]})), encoding="utf-8")
    assert dss.settle_baseline(path, original) == dss.BASELINE_UPDATED
    data = path.read_bytes()
    assert b"\r\n" not in data and b"\\\\" not in data


def test_baseline_path_parsing() -> None:
    assert dss.baseline_path(["--baseline", ".secrets.baseline", "f.py"]) == Path(".secrets.baseline")
    assert dss.baseline_path(["--baseline=x.json"]) == Path("x.json")
    assert dss.baseline_path(["f.py"]) is None
