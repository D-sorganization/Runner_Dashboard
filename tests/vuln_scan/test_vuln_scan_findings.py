"""Findings store: strict schema, fingerprint dedup, suppressions, triage audit (#2569)."""

from __future__ import annotations

from pathlib import Path

import pytest
from vuln_scan.findings import (
    CAPS,
    FindingError,
    FindingsStore,
    fingerprint,
    validate_finding,
)

RAW = {
    "title": "Path traversal in model loader",
    "cwe": "cwe-22",
    "severity": "High",
    "confidence": "medium",
    "path": "./src/loader.py",
    "symbol": "load_model",
    "root_cause": "user path joined without normalisation",
    "repro": "python -c ...",
    "patch": "--- a/src/loader.py",
}
SHA = "0bb24108aa"


@pytest.fixture
def store(tmp_path: Path) -> FindingsStore:
    return FindingsStore(tmp_path / "findings.db")


def test_validate_normalises_fields() -> None:
    finding = validate_finding(RAW)
    assert finding.cwe == "CWE-22"
    assert finding.severity == "high"
    assert finding.path == "src/loader.py"


@pytest.mark.parametrize(
    ("patch", "needle"),
    [
        ({"cwe": "22"}, "cwe"),
        ({"severity": "urgent"}, "severity"),
        ({"confidence": "certain"}, "confidence"),
        ({"path": "/etc/passwd"}, "path"),
        ({"path": "../x.py"}, "path"),
        ({"title": ""}, "title is required"),
        ({"root_cause": 5}, "must be a string"),
        ({"title": "x" * (CAPS["title"] + 1)}, "exceeds"),
    ],
)
def test_validate_rejects(patch: dict, needle: str) -> None:
    with pytest.raises(FindingError, match=needle):
        validate_finding({**RAW, **patch})


def test_validate_rejects_non_mapping() -> None:
    with pytest.raises(FindingError):
        validate_finding(["not", "a", "finding"])


def test_fingerprint_ignores_wording_and_path_spelling() -> None:
    a = validate_finding(RAW)
    b = validate_finding({**RAW, "root_cause": "different words", "path": "src//loader.py"})
    assert a.fingerprint == b.fingerprint == fingerprint("CWE-22", "src/loader.py", "load_model")
    assert a.fingerprint != validate_finding({**RAW, "symbol": "other"}).fingerprint


def test_record_dedups_and_counts_occurrences(store: FindingsStore) -> None:
    finding = validate_finding(RAW)
    first, created = store.record("Tools", finding, scan_sha=SHA)
    again, created_again = store.record("Tools", finding, scan_sha="abcdef1")
    assert created and not created_again and first == again
    row = store.get(first)
    assert row["occurrences"] == 2 and row["scan_sha"] == "abcdef1"
    # The same fingerprint in another repository is a different finding.
    _, other = store.record("UpstreamDrift", finding, scan_sha=SHA)
    assert other


def test_record_rejects_bad_provenance(store: FindingsStore) -> None:
    finding = validate_finding(RAW)
    with pytest.raises(FindingError):
        store.record("Tools", finding, scan_sha="main")
    with pytest.raises(FindingError):
        store.record("Tools", finding, scan_sha=SHA, image_digest="latest")


def test_suppression_persists_and_fixed_reopens(store: FindingsStore) -> None:
    finding = validate_finding(RAW)
    fid, _ = store.record("Tools", finding, scan_sha=SHA)
    store.triage(fid, "false_positive", actor="admin", note="input is trusted")
    store.record("Tools", finding, scan_sha=SHA)
    assert store.get(fid)["state"] == "false_positive"
    store.triage(fid, "fixed", actor="admin")
    store.record("Tools", finding, scan_sha=SHA)
    assert store.get(fid)["state"] == "new"


def test_triage_rules_and_audit(store: FindingsStore) -> None:
    fid, _ = store.record("Tools", validate_finding(RAW), scan_sha=SHA)
    with pytest.raises(FindingError, match="note is required"):
        store.triage(fid, "accepted_risk", actor="admin")
    with pytest.raises(FindingError, match="state must be"):
        store.triage(fid, "deleted", actor="admin")
    with pytest.raises(KeyError):
        store.triage("0" * 32, "confirmed", actor="admin")
    store.triage(fid, "confirmed", actor="alice", note="reproduced")
    audit = store.audit(fid)
    assert [(a["from_state"], a["to_state"], a["actor"]) for a in audit] == [("new", "confirmed", "alice")]


def test_list_orders_by_severity_and_counts_open(store: FindingsStore) -> None:
    store.record("Tools", validate_finding({**RAW, "severity": "low", "symbol": "a"}), scan_sha=SHA)
    crit, _ = store.record("Tools", validate_finding({**RAW, "severity": "critical", "symbol": "b"}), scan_sha=SHA)
    fp, _ = store.record("Tools", validate_finding({**RAW, "symbol": "c"}), scan_sha=SHA)
    store.triage(fp, "false_positive", actor="admin", note="n/a")
    rows = store.list(repo="Tools")
    assert rows[0]["id"] == crit
    assert store.list(state="false_positive")[0]["id"] == fp
    assert store.open_counts() == {"Tools": {"critical": 1, "low": 1}}
