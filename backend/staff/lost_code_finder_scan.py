"""Lost Code Finder scan harness — tier 1/2 signal collection into the evidence ledger (#3868).

Tier 1: deterministic, zero-token tools (vulture text parse, local import graph).
Tier 2: Cartographer source cross-check plus git origin metadata (callers pass
``git log`` / ``git log -S`` citations via ``Tier2Context`` — no network in tests).
Tier 3: intentionally not invoked here; intent inference stays behind
``allow_tier3`` and subscription CLI adapters (never pay-per-use APIs).

The harness **writes only** validated ledger documents (see ``lost_code_finder_ledger``).
"""

from __future__ import annotations

import ast
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from knowledge_pack.cartographer import _CONTRACT_SOURCES, _DOC_SOURCES
from staff.lost_code_finder_ledger import (
    INDIRECTION_CHECK_IDS,
    VERDICT_SAVE,
    VERDICT_UNKNOWN,
    coerce_verdict_for_indirection,
    validate_finding,
    validate_ledger_document,
)

_VULTURE_RE = re.compile(
    r"^(?P<path>.+?):(?P<line>\d+): unused (?:function|class|variable|import|attribute) "
    r"'(?P<symbol>[^']+)' \((?P<pct>\d+)% confidence\)"
)


@dataclass(frozen=True)
class Tier2Context:
    """Tier-2 evidence supplied by the caller (git + Cartographer maps)."""

    head_commit: str
    cartographer_hits: Sequence[str] = ()
    blame_author: str | None = None
    log_s_snippet: str | None = None
    linked_pr: int | None = None
    linked_issue: int | None = None
    docstring: str | None = None


def cartographer_source_globs() -> tuple[str, ...]:
    """Return the fixed Cartographer allowlist globs (maps + contract seams)."""
    return tuple(_DOC_SOURCES) + tuple(_CONTRACT_SOURCES)


def parse_vulture_lines(lines: Sequence[str]) -> list[dict[str, Any]]:
    """Parse ``vulture`` stdout into candidate dicts (tier 1, zero tokens)."""
    out: list[dict[str, Any]] = []
    for line in lines:
        match = _VULTURE_RE.match(line.strip())
        if not match:
            continue
        out.append(
            {
                "path": match.group("path").replace("\\", "/"),
                "symbol": match.group("symbol"),
                "confidence": int(match.group("pct")) / 100.0,
                "evidence": line.strip(),
            }
        )
    return out


def _module_path_for_file(repo_root: Path, file_path: Path) -> str:
    rel = file_path.relative_to(repo_root).as_posix()
    if rel.endswith(".py"):
        rel = rel[: -len(".py")]
    return rel.replace("/", ".")


def tier1_import_references(repo_root: Path, paths: Sequence[str]) -> dict[str, list[str]]:
    """Build a local import/reference graph for ``paths`` (stdlib ``ast`` only)."""
    refs: dict[str, list[str]] = {p: [] for p in paths}
    py_files = list(repo_root.rglob("*.py"))
    for py in py_files:
        rel = py.relative_to(repo_root).as_posix()
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        for target in paths:
            mod = _module_path_for_file(repo_root, repo_root / target)
            if any(mod == imp or mod.startswith(f"{imp}.") or imp.startswith(f"{mod}.") for imp in imported):
                refs[target].append(rel)
    return refs


def _default_cleared_checks(*, cartographer_hit: bool, import_refs: Sequence[str], docstring: str | None) -> list[str]:
    cleared: set[str] = {"dynamic_dispatch", "plugin_registry", "reflection", "config_entry_point", "test_fixture"}
    if cartographer_hit or (docstring and "shim" in docstring.lower()):
        cleared.add("reexport_shim")
    if import_refs:
        cleared.add("reexport_shim")
    return sorted(cleared)


def build_finding_from_candidate(
    candidate: Mapping[str, Any],
    *,
    tier2: Tier2Context,
    import_refs: Sequence[str],
    clear_all_indirection: bool = False,
) -> dict[str, Any]:
    """Merge tier 1/2 signals into one ledger row (fail-closed by default)."""
    path = str(candidate["path"])
    symbol = str(candidate["symbol"])
    cart_hit = path in tier2.cartographer_hits or any(path.endswith(h) for h in tier2.cartographer_hits)
    cleared = (
        list(INDIRECTION_CHECK_IDS)
        if clear_all_indirection
        else _default_cleared_checks(cartographer_hit=cart_hit, import_refs=import_refs, docstring=tier2.docstring)
    )
    evidence_parts = [str(candidate.get("evidence") or "tier1 unused symbol")]
    if tier2.log_s_snippet:
        evidence_parts.append(f"git log -S: {tier2.log_s_snippet}")
    if tier2.docstring:
        evidence_parts.append(f"module doc: {tier2.docstring.strip()}")
    if import_refs:
        evidence_parts.append(f"imported from: {', '.join(import_refs)}")
    if cart_hit:
        evidence_parts.append("listed in Silent Cartographer fixed source allowlist")

    verdict = VERDICT_UNKNOWN
    if clear_all_indirection and cart_hit and tier2.docstring and "shim" in tier2.docstring.lower():
        verdict = VERDICT_SAVE
    elif clear_all_indirection and not import_refs and not cart_hit:
        verdict = VERDICT_UNKNOWN

    row: dict[str, Any] = {
        "path": path,
        "symbol": symbol,
        "evidence": "; ".join(evidence_parts),
        "origin": {
            "commit": tier2.head_commit,
            "pr": tier2.linked_pr,
            "issue": tier2.linked_issue,
        },
        "inferred_purpose": tier2.docstring or "Unknown without tier-3 inference.",
        "replacement": None,
        "verdict": verdict,
        "confidence": float(candidate.get("confidence") or 0.0),
        "links": [f"commit:{tier2.head_commit}"],
        "indirection_checks_cleared": cleared,
    }
    coerced = coerce_verdict_for_indirection(row)
    if validate_finding(coerced):
        coerced["verdict"] = VERDICT_UNKNOWN
    return coerced


def run_repo_scan(
    repo: str,
    repo_root: Path,
    *,
    tier1_vulture_lines: Sequence[str],
    tier2: Tier2Context,
    allow_tier3: bool = False,
) -> dict[str, Any]:
    """Run tier 1/2 scan and return a ledger document ready for persistence."""
    if allow_tier3:
        raise NotImplementedError("tier-3 subscription CLI inference is not enabled in the harness yet")
    candidates = parse_vulture_lines(tier1_vulture_lines)
    paths = [c["path"] for c in candidates]
    import_graph = tier1_import_references(repo_root, paths)
    findings = [
        build_finding_from_candidate(
            cand,
            tier2=tier2,
            import_refs=import_graph.get(str(cand["path"]), []),
        )
        for cand in candidates
    ]
    doc = {"repo": repo, "findings": findings}
    problems = validate_ledger_document(doc)
    if problems:
        raise ValueError("; ".join(problems))
    return doc
