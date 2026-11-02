"""Lost Code Finder evidence ledger contract (#3867).

Each finding records path/symbol, disconnect evidence, git/PR/issue origin,
inferred purpose, optional replacement, verdict, confidence, links, and which
mandatory indirection checks were cleared. The schema fails closed: uncleared
mandatory checks force ``UNKNOWN`` — never ``RETIRE`` or overconfident ``SAVE``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

VERDICT_IMPLEMENTED = "IMPLEMENTED"
VERDICT_SAVE = "SAVE"
VERDICT_RETIRE = "RETIRE"
VERDICT_UNKNOWN = "UNKNOWN"
VERDICTS: frozenset[str] = frozenset({VERDICT_IMPLEMENTED, VERDICT_SAVE, VERDICT_RETIRE, VERDICT_UNKNOWN})

INDIRECTION_CHECK_IDS: frozenset[str] = frozenset(
    {
        "dynamic_dispatch",
        "plugin_registry",
        "reflection",
        "config_entry_point",
        "test_fixture",
        "reexport_shim",
    }
)

_REQUIRED_FINDING_KEYS: frozenset[str] = frozenset(
    {
        "path",
        "symbol",
        "evidence",
        "origin",
        "inferred_purpose",
        "verdict",
        "confidence",
        "links",
        "indirection_checks_cleared",
    }
)


def _non_empty_str(value: Any, field: str, problems: list[str]) -> str:
    if not isinstance(value, str) or not value.strip():
        problems.append(f"{field} must be a non-empty string")
        return ""
    return value.strip()


def _parse_cleared_checks(value: Any, problems: list[str]) -> set[str]:
    if not isinstance(value, list):
        problems.append("indirection_checks_cleared must be a list of check ids")
        return set()
    cleared: set[str] = set()
    for i, item in enumerate(value):
        if not isinstance(item, str) or item not in INDIRECTION_CHECK_IDS:
            problems.append(f"indirection_checks_cleared[{i}] must be one of {sorted(INDIRECTION_CHECK_IDS)}")
            continue
        cleared.add(item)
    return cleared


def uncleared_indirection_checks(cleared: set[str]) -> set[str]:
    """Mandatory checks not present in ``cleared``."""
    return INDIRECTION_CHECK_IDS - cleared


def coerce_verdict_for_indirection(finding: Mapping[str, Any]) -> dict[str, Any]:
    """Return a copy whose verdict is ``UNKNOWN`` when mandatory indirection checks are missing."""
    out = dict(finding)
    cleared = _parse_cleared_checks(out.get("indirection_checks_cleared"), [])
    if uncleared_indirection_checks(cleared):
        out["verdict"] = VERDICT_UNKNOWN
    return out


def validate_finding(data: Mapping[str, Any]) -> list[str]:
    """Validate one ledger row. Empty list means the row satisfies the contract."""
    problems: list[str] = []
    if not isinstance(data, Mapping):
        return ["finding must be a mapping"]

    for key in _REQUIRED_FINDING_KEYS:
        if key not in data:
            problems.append(f"{key} is required")

    _non_empty_str(data.get("path"), "path", problems)
    _non_empty_str(data.get("symbol"), "symbol", problems)
    _non_empty_str(data.get("evidence"), "evidence", problems)
    _non_empty_str(data.get("inferred_purpose"), "inferred_purpose", problems)

    origin = data.get("origin")
    if not isinstance(origin, Mapping):
        problems.append("origin must be an object with commit, pr, and issue keys")
    else:
        if not _non_empty_str(origin.get("commit"), "origin.commit", problems):
            pass
        for optional in ("pr", "issue"):
            if optional in origin and origin[optional] is not None and not isinstance(origin[optional], int):
                problems.append(f"origin.{optional} must be an integer or null")

    replacement = data.get("replacement")
    if replacement is not None and not isinstance(replacement, str):
        problems.append("replacement must be a string or null")

    verdict = data.get("verdict")
    if verdict not in VERDICTS:
        problems.append(f"verdict must be one of {sorted(VERDICTS)}")

    confidence = data.get("confidence")
    if not isinstance(confidence, int | float) or not 0.0 <= float(confidence) <= 1.0:
        problems.append("confidence must be a number between 0 and 1")

    links = data.get("links")
    if not isinstance(links, list) or not links:
        problems.append("links must be a non-empty list")
    elif not all(isinstance(link, str) and link.strip() for link in links):
        problems.append("links must be non-empty strings")

    cleared = _parse_cleared_checks(data.get("indirection_checks_cleared"), problems)
    missing = uncleared_indirection_checks(cleared)
    if missing:
        if verdict != VERDICT_UNKNOWN:
            problems.append(f"verdict must be {VERDICT_UNKNOWN} when indirection checks uncleared: {sorted(missing)}")
        if verdict == VERDICT_RETIRE:
            problems.append(f"{VERDICT_RETIRE} forbidden when indirection checks uncleared: {sorted(missing)}")

    if verdict == VERDICT_RETIRE and missing:
        problems.append(f"{VERDICT_RETIRE} requires all indirection checks cleared")

    return problems


def validate_ledger_document(doc: Mapping[str, Any]) -> list[str]:
    """Validate a persisted ledger document ``{repo, findings: [...]}``."""
    problems: list[str] = []
    if not isinstance(doc, Mapping):
        return ["ledger document must be a mapping"]
    _non_empty_str(doc.get("repo"), "repo", problems)
    findings = doc.get("findings")
    if not isinstance(findings, list):
        problems.append("findings must be a list")
        return problems
    for i, row in enumerate(findings):
        if not isinstance(row, Mapping):
            problems.append(f"findings[{i}] must be an object")
            continue
        for err in validate_finding(row):
            problems.append(f"findings[{i}]: {err}")
    return problems


def findings_sequence(doc: Mapping[str, Any]) -> Sequence[Mapping[str, Any]]:
    """Return findings after coercion — convenience for writers (#3868 harness)."""
    raw = doc.get("findings")
    if not isinstance(raw, list):
        return ()
    out: list[Mapping[str, Any]] = []
    for row in raw:
        if isinstance(row, Mapping):
            out.append(coerce_verdict_for_indirection(row))
    return tuple(out)
