"""Local pre-PR merge gates (#2655), exercised with the 2026-10-23 main failures."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts import verify_merge_gates as gates

ROOT = Path(__file__).resolve().parents[1]
# PR #2628 merged with `TypeScript typecheck (tsc)` red and broke main (run 40034619873).
PR_2628_FILES = [
    "backend/routers/staff_v1.py",
    "backend/staff/cloud_dispatch.py",
    "changes/2627-staff-cloud-session-dispatch.md",
    "frontend/src/lib/openapi.json",
    "tests/staff/test_cloud_dispatch.py",
    "uv.lock",
]
# Findings CI Secrets reported on main in run 39994915171 (SHA-1 digests, not values).
LIBRARY_PAGE_HASH = "ea3fa2bbf7dc31620d02a796849747ab14dd7474"  # pragma: allowlist secret
HEALTH_SPOKE_HASH = "6d8e1d74b402168476b5813e898d1eba7a97c70a"  # pragma: allowlist secret
LIBRARY_PAGE = {
    "type": "Base64 High Entropy String",
    "filename": "frontend/src/pages/__tests__/LibraryPage.test.tsx",
    "hashed_secret": LIBRARY_PAGE_HASH,
    "is_verified": False,
    "line_number": 93,
}
HEALTH_SPOKE = {
    "type": "Secret Keyword",
    "filename": "tests/test_health_spoke.py",
    "hashed_secret": HEALTH_SPOKE_HASH,
    "is_verified": False,
    "line_number": 137,
}


def _baseline(*findings: dict[str, Any], windows: bool = False) -> dict[str, Any]:
    results: dict[str, list[dict[str, Any]]] = {}
    for finding in findings:
        name = finding["filename"].replace("/", "\\") if windows else finding["filename"]
        results.setdefault(name, []).append({**finding, "filename": name})
    return {"version": "1.5.0", "results": results}


def test_run_39994915171_drift_reports_new_and_stale_findings() -> None:
    drift = gates.baseline_drift(_baseline(HEALTH_SPOKE), _baseline(LIBRARY_PAGE))
    assert drift == [
        f"new: {LIBRARY_PAGE['filename']}:93 Base64 High Entropy String {LIBRARY_PAGE['hashed_secret']}",
        f"stale: tests/test_health_spoke.py:137 Secret Keyword {HEALTH_SPOKE['hashed_secret']}",
    ]


def test_windows_written_baseline_is_not_drift() -> None:
    assert gates.baseline_drift(_baseline(LIBRARY_PAGE, windows=True), _baseline(LIBRARY_PAGE)) == []


def _fake_scanner(after: dict[str, Any]) -> Any:
    def run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        assert command[-4:-2] != ["--baseline", ".secrets.baseline"]
        assert command[-2:] == ["--exclude-files", gates.EXCLUDE_FILES]
        Path(command[command.index("--baseline") + 1]).write_text(json.dumps(after), encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "", "")

    return run


def test_audit_fails_closed_without_rewriting_committed_baseline(tmp_path: Path) -> None:
    committed = json.dumps(_baseline(HEALTH_SPOKE))
    (tmp_path / ".secrets.baseline").write_text(committed, encoding="utf-8")
    drift = gates.audit_baseline(
        tmp_path, scanner_python="py", run=_fake_scanner(_baseline(HEALTH_SPOKE, LIBRARY_PAGE))
    )
    assert drift == [f"new: {LIBRARY_PAGE['filename']}:93 Base64 High Entropy String {LIBRARY_PAGE['hashed_secret']}"]
    assert (tmp_path / ".secrets.baseline").read_text(encoding="utf-8") == committed


def test_audit_fails_closed_when_scanner_or_baseline_missing(tmp_path: Path) -> None:
    assert gates.audit_baseline(tmp_path) == ["missing .secrets.baseline"]
    (tmp_path / ".secrets.baseline").write_text("{}", encoding="utf-8")

    def broken(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 1, "", "No module named detect_secrets")

    assert gates.audit_baseline(tmp_path, scanner_python="py", run=broken) == [
        "detect-secrets scan failed (1): No module named detect_secrets"
    ]


def test_contract_gate_runs_regeneration_check_for_pr_2628() -> None:
    calls: list[list[str]] = []

    def run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 1, "", "--- frontend/src/lib/openapi.json")

    problems = gates.check_contract(ROOT, PR_2628_FILES, run=run)
    assert calls == [["bash", "scripts/gen-api-client.sh", "--check"]]
    assert problems and "openapi.json" in problems[0] and "npm run generate-api" in problems[0]
    assert gates.check_contract(ROOT, ["docs/operations/readme.md"], run=run) == []
    assert len(calls) == 1


def _ci_classifier_module() -> Any:
    path = ROOT / "tests/frontend/test_api_generation_contract.py"
    spec = importlib.util.spec_from_file_location("_api_contract_ci", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "changed",
    [PR_2628_FILES, ["docs/operations/readme.md"], ["package.json"], ["scripts/gen-api-client.sh"], ["config/x.json"]],
)
def test_local_contract_scope_matches_frontend_tests_classifier(changed: list[str]) -> None:
    ci = _ci_classifier_module()
    result = ci._run_api_scope_classifier(ci._api_scope_classifier(), changed)
    assert result.returncode == 0, result.stderr
    outputs = dict(line.split("=", 1) for line in result.stdout.splitlines())
    assert (outputs["run_api_contract"] == "true") is gates.touches_api_contract(changed)


def test_changed_paths_include_committed_uncommitted_and_untracked(tmp_path: Path) -> None:
    def git(*args: str) -> None:
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=tmp_path, check=True)

    git("init", "-q", "-b", "main")
    (tmp_path / "README.md").write_text("x", encoding="utf-8")
    git("add", ".")
    git("commit", "-qm", "base")
    git("checkout", "-qb", "feature")
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend/app.py").write_text("x", encoding="utf-8")
    git("add", ".")
    git("commit", "-qm", "api")
    (tmp_path / "README.md").write_text("y", encoding="utf-8")
    (tmp_path / "new.txt").write_text("z", encoding="utf-8")
    assert gates.changed_paths(tmp_path, "main") == ["README.md", "backend/app.py", "new.txt"]


def test_ci_secrets_and_frontend_gates_run_on_merge_path() -> None:
    secrets_wf = yaml.safe_load((ROOT / ".github/workflows/ci-secrets.yml").read_text(encoding="utf-8"))
    frontend_wf = yaml.safe_load((ROOT / ".github/workflows/frontend-tests.yml").read_text(encoding="utf-8"))
    for workflow in (secrets_wf, frontend_wf):
        assert {"pull_request", "merge_group"} <= set(workflow[True])
    audit = next(
        s for s in secrets_wf["jobs"]["detect-secrets"]["steps"] if s.get("name") == "Audit baseline integrity"
    )
    assert 'scripts/verify_merge_gates.py --only baseline --scanner-python "$PY"' in audit["run"]
    assert "scan --baseline .secrets.baseline" not in audit["run"]
    supplement = json.loads((ROOT / "config/repository_rulesets/required-status-checks-supplement.json").read_text())
    rule = next(r for r in supplement["rules"] if r["type"] == "required_status_checks")
    contexts = {check["context"] for check in rule["parameters"]["required_status_checks"]}
    assert {"TypeScript typecheck (tsc)", "detect-secrets (baseline diff)", "frontend-required"} <= contexts


def test_cli_reports_baseline_drift(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    (tmp_path / ".secrets.baseline").write_text(json.dumps(_baseline()), encoding="utf-8")
    monkeypatch.setattr(gates.subprocess, "run", _fake_scanner(_baseline(LIBRARY_PAGE)))
    assert (
        gates.main(["--repository-root", str(tmp_path), "--only", "baseline", "--scanner-python", sys.executable]) == 1
    )
    assert "LibraryPage.test.tsx:93" in capsys.readouterr().err
