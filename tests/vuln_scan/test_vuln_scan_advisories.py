"""Draft GHSA promotion, the protected-patch guard and SARIF export (#2573)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from vuln_scan import advisories
from vuln_scan.patches import patch_paths, protected_paths
from vuln_scan.sarif import FINGERPRINT_KEY, to_sarif

ORG = "D-sorganization"
GHSA = "GHSA-2xq9-c4vm-wh7p"
ROW: dict[str, Any] = {
    "id": "a" * 32,
    "repo": "Tools",
    "fingerprint": "f" * 64,
    "title": "Command injection in export",
    "cwe": "CWE-78",
    "severity": "high",
    "confidence": "high",
    "path": "src/export.py",
    "symbol": "export",
    "root_cause": "shell=True with ``` user input",
    "repro": "run export ';id'",
    "patch": "--- a/src/export.py\n+++ b/src/export.py\n@@\n-x\n+y\n",
    "state": "confirmed",
    "scan_sha": "0bb24108aa" * 4,
}
WORKFLOW_PATCH = (
    "diff --git a/.github/workflows/ci.yml b/.github/workflows/ci.yml"
    + chr(10)
    + "--- a/.github/workflows/ci.yml"
    + chr(10)
    + "+++ b/.github/workflows/ci.yml"
    + chr(10)
)


class FakeWriter:
    def __init__(self, response: dict[str, Any] | None = None, fail: bool = False) -> None:
        self.calls: list[tuple[str, str, Any]] = []
        self.response = response or {
            "ghsa_id": GHSA,
            "html_url": f"https://github.com/{ORG}/Tools/security/advisories/{GHSA}",
        }
        self.fail = fail

    async def __call__(self, endpoint: str, method: str, body: Any) -> dict[str, Any]:
        self.calls.append((endpoint, method, body))
        if self.fail:
            raise RuntimeError("GitHub 403")
        return self.response


def _create(store: advisories.AdvisoryStore, row: dict[str, Any], write: FakeWriter) -> dict[str, Any]:
    return asyncio.run(advisories.create_draft(row, org=ORG, actor="op", store=store, write=write))


def test_draft_uses_a_private_fork_and_records_one_advisory(tmp_path: Path) -> None:
    store, write = advisories.AdvisoryStore(tmp_path / "db"), FakeWriter()
    created = _create(store, ROW, write)
    assert created["ghsa_id"] == GHSA and created["actor"] == "op"
    endpoint, method, body = write.calls[0]
    assert endpoint == f"/repos/{ORG}/Tools/security-advisories" and method == "POST"
    assert body["start_private_fork"] is True
    assert body["severity"] == "high" and body["cwe_ids"] == ["CWE-78"]
    assert body["vulnerabilities"] == [{"package": {"ecosystem": "other", "name": "Tools"}}]
    assert "````" in body["description"]  # fence outgrows the backticks in model text
    with pytest.raises(advisories.AdvisoryExists):
        _create(store, ROW, write)
    assert len(write.calls) == 1


@pytest.mark.parametrize(
    "patch, reason",
    [
        ({"state": "new"}, "confirmed"),
        ({"state": "inconclusive"}, "confirmed"),
        ({"severity": "medium"}, "high or critical"),
    ],
)
def test_only_confirmed_high_or_critical_findings_qualify(tmp_path: Path, patch: dict[str, Any], reason: str) -> None:
    write = FakeWriter()
    with pytest.raises(advisories.AdvisoryError, match=reason):
        _create(advisories.AdvisoryStore(tmp_path / "db"), {**ROW, **patch}, write)
    assert write.calls == []


def test_failed_or_odd_github_responses_release_the_reservation(tmp_path: Path) -> None:
    store = advisories.AdvisoryStore(tmp_path / "db")
    with pytest.raises(RuntimeError):
        _create(store, ROW, FakeWriter(fail=True))
    with pytest.raises(advisories.AdvisoryError):
        _create(store, ROW, FakeWriter(response={"ghsa_id": "nope", "html_url": "https://evil.example/x"}))
    assert store.get(ROW["id"]) is None
    assert _create(store, ROW, FakeWriter())["ghsa_id"] == GHSA


def test_protected_patch_is_left_out_of_the_advisory() -> None:
    body = advisories.advisory_body({**ROW, "patch": WORKFLOW_PATCH}, "Tools")
    assert ".github/workflows/ci.yml" in body["description"] and "Omitted" in body["description"]
    assert "+++ b/.github" not in body["description"]


def test_patch_paths_and_protected_paths() -> None:
    assert patch_paths(WORKFLOW_PATCH) == [".github/workflows/ci.yml"]
    assert protected_paths(WORKFLOW_PATCH) == [".github/workflows/ci.yml"]
    assert protected_paths("--- a/.OSS-Scanner/Dockerfile\n+++ b/.OSS-Scanner/Dockerfile\n") == [
        ".OSS-Scanner/Dockerfile"
    ]
    assert protected_paths(ROW["patch"]) == []
    assert protected_paths("--- /dev/null\n+++ b/src/new.py\n") == []
    assert protected_paths("--- ./.github/x\n+++ ./.github/x\n") == [".github/x"]


def test_sarif_has_open_findings_only_and_never_repro_or_patch() -> None:
    rows = [
        ROW,
        {**ROW, "id": "b" * 32, "state": "false_positive", "fingerprint": "e" * 64},
        {**ROW, "id": "c" * 32, "state": "fixed", "fingerprint": "d" * 64},
        {**ROW, "id": "d" * 32, "repo": "UpstreamDrift", "severity": "low", "symbol": "", "state": "new"},
    ]
    log = to_sarif(rows)
    assert log["version"] == "2.1.0"
    assert [r["automationDetails"]["id"] for r in log["runs"]] == [
        "fleet-security-scanner/Tools/",
        "fleet-security-scanner/UpstreamDrift/",
    ]
    tools_run = log["runs"][0]
    assert len(tools_run["results"]) == 1
    result = tools_run["results"][0]
    assert result["level"] == "error" and result["partialFingerprints"][FINGERPRINT_KEY] == ROW["fingerprint"]
    assert result["properties"]["security-severity"] == "8.0"
    assert tools_run["tool"]["driver"]["rules"][0]["helpUri"].endswith("/78.html")
    text = str(log)
    assert ROW["repro"] not in text and "+y" not in text
    assert "logicalLocations" not in log["runs"][1]["results"][0]["locations"][0]
