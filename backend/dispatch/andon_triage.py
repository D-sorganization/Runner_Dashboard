"""Andon triage in the existing Barb dispatcher lane (#2903, epic #2900 A4).

Ranks open deduped andon records (and ``gate-error`` escalations from A6/A7),
builds issue-shaped triage cards under ``barb:issue-triage`` with a single
``claim:<agent>`` label and a single linked fingerprint, and auto-dispatches
red-main P0 failures at most once per fingerprint.
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import Any, Final

_REPO_ROOT = Path(__file__).resolve().parents[2]
_FLEET_CAP = _REPO_ROOT / ".github" / "fleet-pr-cap"


def _load_module_from_path(qualified_name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(qualified_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[qualified_name] = module
    spec.loader.exec_module(module)
    return module


def _load_fleet_agent_identity() -> ModuleType:
    """Load fleet agent identity even when repo-root ``shared_scripts`` is already imported."""
    pr_key = "shared_scripts.pr_agent_identity"
    if pr_key not in sys.modules:
        _load_module_from_path(pr_key, _FLEET_CAP / "shared_scripts" / "pr_agent_identity.py")
    return _load_module_from_path("fleet_agent_identity", _FLEET_CAP / "shared_scripts" / "agent_identity.py")


_agent_identity = _load_fleet_agent_identity()
CLAIM_LABEL_PREFIX: Final[str] = str(_agent_identity.CLAIM_LABEL_PREFIX)
PROFILES: Final[dict[str, Any]] = dict(_agent_identity.PROFILES)
claim_label: Callable[[str], str] = _agent_identity.claim_label

BARB_TRIAGE_SCOPE: Final[str] = "barb:issue-triage"

SEVERITY_ORDER: Final[dict[str, int]] = {
    "p0": 0,
    "critical": 1,
    "high": 2,
    "medium": 3,
    "low": 4,
}


def _parse_iso_utc(raw: str) -> datetime:
    text = str(raw or "").strip()
    if not text:
        raise ValueError("::error::AndonRecord opened_at must be a non-empty ISO-8601 timestamp")
    normalized = text[:-1] + "+00:00" if text.endswith(("Z", "z")) else text
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"::error::Invalid ISO-8601 opened_at timestamp: {raw!r}") from exc
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)  # noqa: UP017
    return parsed.astimezone(timezone.utc)  # noqa: UP017


@dataclass(frozen=True)
class AndonRecord:
    """Normalized open andon record ready for dispatcher ranking and triage."""

    fingerprint: str
    issue_number: int
    opened_at: str
    red_main: bool = False
    severity: str = "high"
    repo: str = "D-sorganization/Runner_Dashboard"
    title: str = ""
    summary: str = ""
    source: str = "andon"
    acceptance_criteria: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        fp = str(self.fingerprint or "").strip()
        if not fp:
            raise ValueError("::error::AndonRecord requires a non-empty fingerprint")
        object.__setattr__(self, "fingerprint", fp)

        if isinstance(self.issue_number, bool) or not isinstance(self.issue_number, int) or self.issue_number <= 0:
            raise ValueError(
                f"::error::AndonRecord requires a positive integer issue_number, got {self.issue_number!r}"
            )

        _parse_iso_utc(self.opened_at)

        sev = str(self.severity or "high").strip().lower()
        if not sev:
            raise ValueError("::error::AndonRecord severity must not be empty")
        object.__setattr__(self, "severity", sev)

        src = str(self.source or "andon").strip().lower()
        if not src:
            raise ValueError("::error::AndonRecord source must not be empty")
        object.__setattr__(self, "source", src)

        repo_name = str(self.repo or "").strip()
        if not repo_name:
            raise ValueError("::error::AndonRecord repo must not be empty")
        object.__setattr__(self, "repo", repo_name)

        criteria = tuple(str(item).strip() for item in self.acceptance_criteria if str(item).strip())
        object.__setattr__(self, "acceptance_criteria", criteria)

    @property
    def opened_at_dt(self) -> datetime:
        """Return ``opened_at`` parsed as a UTC ``datetime``."""
        return _parse_iso_utc(self.opened_at)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> AndonRecord:
        """Construct and validate an ``AndonRecord`` from a mapping."""
        raw_criteria = data.get("acceptance_criteria") or ()
        criteria_tuple = (raw_criteria,) if isinstance(raw_criteria, str) else tuple(raw_criteria)
        return cls(
            fingerprint=str(data.get("fingerprint") or ""),
            issue_number=int(data.get("issue_number") or 0),
            opened_at=str(data.get("opened_at") or ""),
            red_main=bool(data.get("red_main", False)),
            severity=str(data.get("severity") or "high"),
            repo=str(data.get("repo") or "D-sorganization/Runner_Dashboard"),
            title=str(data.get("title") or ""),
            summary=str(data.get("summary") or ""),
            source=str(data.get("source") or "andon"),
            acceptance_criteria=criteria_tuple,
        )


def _coerce_record(item: AndonRecord | Mapping[str, Any]) -> AndonRecord:
    if isinstance(item, AndonRecord):
        return item
    if isinstance(item, Mapping):
        return AndonRecord.from_mapping(item)
    raise TypeError(f"::error::Expected AndonRecord or Mapping, got {type(item).__name__}")


def _severity_rank(record: AndonRecord) -> int:
    if record.red_main:
        return 0
    return SEVERITY_ORDER.get(record.severity, SEVERITY_ORDER["high"])


def rank_andons(records: Sequence[AndonRecord | Mapping[str, Any]]) -> list[AndonRecord]:
    """Rank open andons and ``gate-error`` escalations for ``barb:issue-triage``.

    Sort order:
    1. ``red_main=True`` first (default-branch failures are P0).
    2. Severity rank (``p0`` > ``critical`` > ``high`` > ``medium`` > ``low``).
    3. Older ``opened_at`` timestamp first (FIFO by age within the same tier).
    4. ``issue_number`` and ``fingerprint`` for deterministic tie-breaking.
    """
    validated = [_coerce_record(item) for item in records]
    return sorted(
        validated,
        key=lambda r: (
            0 if r.red_main else 1,
            _severity_rank(r),
            r.opened_at_dt,
            r.issue_number,
            r.fingerprint,
        ),
    )


def _default_acceptance_criteria(record: AndonRecord) -> list[str]:
    if record.acceptance_criteria:
        return list(record.acceptance_criteria)
    return [
        (
            f"Reproduce and fix the root cause for andon #{record.issue_number} "
            f"(fingerprint `{record.fingerprint}`) with a failing test first (TDD)."
        ),
        "All required CI checks pass on the fix PR with diff under 500 changed lines.",
    ]


def build_triage_card(
    record: AndonRecord | Mapping[str, Any],
    *,
    claim_agent: str,
    extra_labels: Sequence[str] = (),
) -> dict[str, Any]:
    """Build a claimable triage card dictionary for one deduped andon record.

    Preconditions:
    - ``record`` satisfies ``AndonRecord`` invariants.
    - ``claim_agent`` is a known, non-phased-out fleet roster id in ``agent_identity.PROFILES``.

    Postconditions:
    - ``card["linked_fingerprints"]`` contains exactly 1 fingerprint.
    - ``card["labels"]`` contains exactly 1 ``claim:<agent>`` label.
    """
    andon = _coerce_record(record)
    agent_id = str(claim_agent or "").strip()
    label = claim_label(agent_id)
    profile = PROFILES[agent_id]
    if profile.phased_out:
        raise ValueError(f"::error::Cannot assign triage card to phased-out agent: {agent_id!r}")

    labels: list[str] = ["andon", "triage-card"]
    if andon.source == "gate-error":
        labels.append("gate-error")
    if andon.red_main or andon.severity == "p0":
        labels.append("p0")
    for extra in extra_labels:
        cleaned = str(extra or "").strip()
        if not cleaned:
            continue
        if cleaned.startswith(CLAIM_LABEL_PREFIX) and cleaned != label:
            raise ValueError(f"::error::Conflicting claim label {cleaned!r} vs {label!r}")
        if cleaned not in labels and cleaned != label:
            labels.append(cleaned)
    labels.append(label)

    criteria = _default_acceptance_criteria(andon)
    priority = "P0" if (andon.red_main or andon.severity == "p0") else "P1"
    card_title = (
        andon.title.strip() if andon.title.strip() else f"[andon-triage] #{andon.issue_number} ({andon.fingerprint})"
    )
    summary_line = andon.summary.strip() or f"Triage card for andon #{andon.issue_number}."
    criteria_md = "\n".join(f"- [ ] {item}" for item in criteria)
    body = "\n".join(
        [
            f"<!-- andon-fingerprint: {andon.fingerprint} -->",
            "## Summary",
            summary_line,
            "",
            "## Linked Andon",
            f"- Source issue: #{andon.issue_number}",
            f"- Fingerprint: `{andon.fingerprint}`",
            f"- Source lane: `{andon.source}`",
            f"- Dispatcher scope: `{BARB_TRIAGE_SCOPE}`",
            f"- Priority: `{priority}` (`red_main={andon.red_main}`)",
            "",
            "## Acceptance Criteria",
            criteria_md,
        ]
    )

    linked_fingerprints = [andon.fingerprint]
    claim_labels = [lbl for lbl in labels if lbl.startswith(CLAIM_LABEL_PREFIX)]
    if len(linked_fingerprints) != 1 or not linked_fingerprints[0]:
        raise RuntimeError("::error::Postcondition failed: triage card must link exactly one fingerprint")
    if claim_labels != [label]:
        raise RuntimeError("::error::Postcondition failed: triage card must carry exactly one claim:<agent> label")

    return {
        "title": card_title,
        "body": body,
        "repo": andon.repo,
        "scope": BARB_TRIAGE_SCOPE,
        "priority": priority,
        "red_main": andon.red_main,
        "source": andon.source,
        "source_issue_number": andon.issue_number,
        "opened_at": andon.opened_at,
        "claim_agent": agent_id,
        "claim_label": label,
        "labels": labels,
        "linked_fingerprints": linked_fingerprints,
        "acceptance_criteria": criteria,
    }


def dispatch_red_main_p0(
    records: Sequence[AndonRecord | Mapping[str, Any]],
    *,
    claim_agent: str = "claude",
    dispatcher: Callable[[dict[str, Any]], Any] | None = None,
    already_dispatched_fingerprints: Iterable[str] = (),
) -> list[dict[str, Any]]:
    """Auto-claim and auto-dispatch red-main P0 andons, deduplicated by fingerprint.

    Validates all input records before emitting any dispatch so invalid records
    fail closed without partial side effects. Emits at most one P0 triage card
    per unique ``fingerprint``.
    """
    ranked = rank_andons(records)
    # Validate claim_agent upfront before any dispatch callback runs.
    _ = claim_label(str(claim_agent or "").strip())

    seen_fingerprints: set[str] = {str(fp).strip() for fp in already_dispatched_fingerprints if str(fp).strip()}
    emitted: list[dict[str, Any]] = []

    for record in ranked:
        if not record.red_main:
            continue
        if record.fingerprint in seen_fingerprints:
            continue
        seen_fingerprints.add(record.fingerprint)
        card = build_triage_card(record, claim_agent=claim_agent)
        if dispatcher is not None:
            dispatcher(card)
        emitted.append(card)

    emitted_fps = [card["linked_fingerprints"][0] for card in emitted]
    if len(emitted_fps) != len(set(emitted_fps)):
        raise RuntimeError("::error::Postcondition failed: duplicate red-main P0 fingerprint dispatched")
    return emitted


__all__ = [
    "AndonRecord",
    "BARB_TRIAGE_SCOPE",
    "SEVERITY_ORDER",
    "build_triage_card",
    "dispatch_red_main_p0",
    "rank_andons",
]
