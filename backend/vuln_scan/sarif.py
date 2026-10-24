"""SARIF 2.1.0 export of open findings (#2573, panel verdict q3).

Export is opt-in (``sarif_export`` in the operator config) because a SARIF file
uploaded to code scanning is visible to everyone with read access to the
repository. To keep an exported file low-risk:

* only open findings (``new``, ``confirmed``, ``inconclusive``) are included;
  suppressed and fixed ones never leave the store;
* reproduction steps and candidate patches are never included, only the title,
  root cause and location;
* each result carries ``partialFingerprints`` with the store's
  ``sha256(CWE, path, symbol)`` fingerprint, so re-uploads deduplicate.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

SARIF_SCHEMA = "https://json.schemastore.org/sarif-2.1.0.json"
OPEN_STATES = frozenset({"new", "confirmed", "inconclusive"})
TOOL_NAME = "fleet-security-scanner"
FINGERPRINT_KEY = "vulnScanFingerprint/v1"
_LEVEL = {
    "critical": "error",
    "high": "error",
    "medium": "warning",
    "low": "note",
    "info": "note",
}
# GitHub code scanning reads ``security-severity`` (0.0-10.0) to rank alerts.
_SCORE = {
    "critical": "9.5",
    "high": "8.0",
    "medium": "5.5",
    "low": "3.0",
    "info": "0.0",
}
MAX_MESSAGE = 1000


def _rule(cwe: str) -> dict[str, Any]:
    number = cwe.split("-", 1)[1]
    return {
        "id": cwe,
        "name": cwe,
        "shortDescription": {"text": cwe},
        "helpUri": f"https://cwe.mitre.org/data/definitions/{number}.html",
        "properties": {"tags": ["security", f"external/cwe/{cwe.lower()}"]},
    }


def _result(finding: Mapping[str, Any]) -> dict[str, Any]:
    text = f"{finding['title']}: {finding['root_cause']}"
    result: dict[str, Any] = {
        "ruleId": finding["cwe"],
        "level": _LEVEL[finding["severity"]],
        "message": {"text": text[:MAX_MESSAGE]},
        "locations": [
            {
                "physicalLocation": {"artifactLocation": {"uri": finding["path"]}},
                **(
                    {"logicalLocations": [{"name": finding["symbol"]}]}
                    if finding.get("symbol")
                    else {}
                ),
            }
        ],
        "partialFingerprints": {FINGERPRINT_KEY: finding["fingerprint"]},
        "properties": {
            "security-severity": _SCORE[finding["severity"]],
            "state": finding["state"],
            "confidence": finding["confidence"],
        },
    }
    return result


def to_sarif(
    findings: Iterable[Mapping[str, Any]], *, version: str = "1.0.0"
) -> dict[str, Any]:
    """A SARIF 2.1.0 log with one run per repository, open findings only."""
    by_repo: dict[str, list[Mapping[str, Any]]] = {}
    for finding in findings:
        if finding.get("state") in OPEN_STATES:
            by_repo.setdefault(str(finding["repo"]), []).append(finding)
    runs = []
    for repo, items in sorted(by_repo.items()):
        cwes = sorted({str(f["cwe"]) for f in items})
        runs.append(
            {
                "tool": {
                    "driver": {
                        "name": TOOL_NAME,
                        "version": version,
                        "rules": [_rule(c) for c in cwes],
                    }
                },
                "automationDetails": {"id": f"{TOOL_NAME}/{repo}/"},
                "results": [_result(f) for f in items],
            }
        )
    return {"$schema": SARIF_SCHEMA, "version": "2.1.0", "runs": runs}
