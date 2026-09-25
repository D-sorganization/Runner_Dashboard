"""Barb routing evaluation framework and regression checking (SC-C7, Issue #1340).

Evaluates routing accuracy against representative benchmark cases, tracks
accuracy metrics per category, records nightly eval results for Board display,
and extracts candidate evaluation cases from operator overrides.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from staff.conversations import ConversationStore, get_conversation_store
from staff.router import BarbRouter, route_deterministic

log = logging.getLogger("dashboard.staff.routing_eval")

DEFAULT_CASES_PATH = Path(__file__).resolve().parents[2] / "tests" / "staff" / "routing_eval" / "cases.json"


def _config_dir() -> Path:
    configured = os.environ.get("RUNNER_DASHBOARD_CONFIG_DIR")
    if configured:
        return Path(configured).expanduser()
    return Path("~/.config/runner-dashboard").expanduser()


def default_eval_result_path() -> Path:
    env_path = os.environ.get("STAFF_ROUTING_EVAL_FILE")
    if env_path:
        return Path(env_path).expanduser()
    return _config_dir() / "routing_eval_latest.json"


@dataclass(frozen=True)
class RoutingEvalCase:
    """One routing evaluation test case."""

    id: str
    prompt: str
    category: str
    expected_role: str | None
    expected_roles: tuple[str, ...] = ()
    expected_clarify: bool = False
    expected_answer_myself: bool = False
    is_code_change: bool = False
    deterministic: bool = True
    expected_mode: str | None = None

    def __post_init__(self) -> None:
        if not self.id or not isinstance(self.id, str):
            raise ValueError("RoutingEvalCase.id must be a non-empty string")
        if not self.prompt or not isinstance(self.prompt, str):
            raise ValueError(f"RoutingEvalCase({self.id}).prompt must be a non-empty string")


@dataclass(frozen=True)
class RoutingEvalResult:
    """Summary of routing evaluation execution and accuracy metrics."""

    evaluated_at: str
    total: int
    passed: int
    accuracy: float
    mode: str
    categories: dict[str, dict[str, Any]] = field(default_factory=dict)
    failures: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "evaluated_at": self.evaluated_at,
            "total": self.total,
            "passed": self.passed,
            "accuracy": round(self.accuracy, 4),
            "mode": self.mode,
            "categories": self.categories,
            "failures": self.failures,
        }


def load_eval_cases(path: Path | None = None) -> list[RoutingEvalCase]:
    """Load and validate evaluation cases from JSON file."""
    case_path = path or DEFAULT_CASES_PATH
    if not case_path.exists():
        raise FileNotFoundError(f"Routing eval cases file not found at: {case_path}")

    raw_data = json.loads(case_path.read_text(encoding="utf-8"))
    if not isinstance(raw_data, list):
        raise ValueError(f"Eval cases at {case_path} must be a JSON array")
    if not raw_data:
        raise ValueError(f"Eval cases at {case_path} must not be empty")

    cases: list[RoutingEvalCase] = []
    seen_ids: set[str] = set()

    for item in raw_data:
        cid = str(item.get("id") or "").strip()
        if cid in seen_ids:
            raise ValueError(f"Duplicate eval case ID: {cid}")
        seen_ids.add(cid)

        roles_list = item.get("expected_roles")
        if isinstance(roles_list, list):
            exp_roles = tuple(str(r) for r in roles_list)
        elif item.get("expected_role"):
            exp_roles = (str(item["expected_role"]),)
        else:
            exp_roles = ()

        cases.append(
            RoutingEvalCase(
                id=cid,
                prompt=str(item.get("prompt") or "").strip(),
                category=str(item.get("category") or "general").strip(),
                expected_role=item.get("expected_role"),
                expected_roles=exp_roles,
                expected_clarify=bool(item.get("expected_clarify", False)),
                expected_answer_myself=bool(item.get("expected_answer_myself", False)),
                is_code_change=bool(item.get("is_code_change", False)),
                deterministic=bool(item.get("deterministic", True)),
                expected_mode=item.get("expected_mode"),
            )
        )

    return cases


def evaluate_deterministic(cases: list[RoutingEvalCase]) -> RoutingEvalResult:
    """Evaluate deterministic pre-router (Stage 1) against test cases."""
    now_iso = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    passed = 0
    failures: list[dict[str, Any]] = []
    cat_stats: dict[str, dict[str, int]] = {}

    for case in cases:
        cat = cat_stats.setdefault(case.category, {"total": 0, "passed": 0})
        cat["total"] += 1

        decision = route_deterministic(case.prompt)

        # Ambiguous cases: pre-router must return None (defer to Stage 2)
        if case.expected_clarify:
            if decision is None:
                passed += 1
                cat["passed"] += 1
            else:
                failures.append(
                    {
                        "id": case.id,
                        "prompt": case.prompt,
                        "reason": f"Expected deferral (None) for ambiguous prompt, got role '{decision.chosen_role}'",
                    }
                )
            continue

        # Regular deterministic cases
        if decision is None:
            failures.append(
                {
                    "id": case.id,
                    "prompt": case.prompt,
                    "reason": f"Pre-router returned None, expected '{case.expected_role}'",
                }
            )
            continue

        role_match = decision.chosen_role in case.expected_roles
        code_match = not case.is_code_change or decision.is_code_change
        answer_match = not case.expected_answer_myself or decision.chosen_role == "barb"

        if role_match and code_match and answer_match:
            passed += 1
            cat["passed"] += 1
        else:
            diffs: list[str] = []
            if not role_match:
                diffs.append(f"role '{decision.chosen_role}' not in {case.expected_roles}")
            if not code_match:
                diffs.append("is_code_change False, expected True")
            if not answer_match:
                diffs.append("expected barb self-handling")
            failures.append(
                {
                    "id": case.id,
                    "prompt": case.prompt,
                    "reason": "; ".join(diffs),
                }
            )

    total = len(cases)
    accuracy = (passed / total) if total > 0 else 0.0

    categories_out = {
        k: {
            "total": v["total"],
            "passed": v["passed"],
            "accuracy": round(v["passed"] / v["total"], 4) if v["total"] > 0 else 0.0,
        }
        for k, v in cat_stats.items()
    }

    return RoutingEvalResult(
        evaluated_at=now_iso,
        total=total,
        passed=passed,
        accuracy=accuracy,
        mode="deterministic",
        categories=categories_out,
        failures=failures,
    )


def evaluate_full(
    cases: list[RoutingEvalCase],
    router: BarbRouter | None = None,
) -> RoutingEvalResult:
    """Evaluate full two-stage router against test cases."""
    r = router or BarbRouter()
    now_iso = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    passed = 0
    failures: list[dict[str, Any]] = []
    cat_stats: dict[str, dict[str, int]] = {}

    for case in cases:
        cat = cat_stats.setdefault(case.category, {"total": 0, "passed": 0})
        cat["total"] += 1

        decision = r.route(case.prompt)

        if case.expected_clarify:
            if decision.needs_clarification or decision.chosen_role is None:
                passed += 1
                cat["passed"] += 1
            else:
                failures.append(
                    {
                        "id": case.id,
                        "prompt": case.prompt,
                        "reason": f"Expected clarification, got role '{decision.chosen_role}'",
                    }
                )
            continue

        role_match = decision.chosen_role in case.expected_roles
        code_match = not case.is_code_change or decision.is_code_change

        if role_match and code_match:
            passed += 1
            cat["passed"] += 1
        else:
            diffs: list[str] = []
            if not role_match:
                diffs.append(f"role '{decision.chosen_role}' not in {case.expected_roles}")
            if not code_match:
                diffs.append("is_code_change False, expected True")
            failures.append(
                {
                    "id": case.id,
                    "prompt": case.prompt,
                    "reason": "; ".join(diffs),
                }
            )

    total = len(cases)
    accuracy = (passed / total) if total > 0 else 0.0

    categories_out = {
        k: {
            "total": v["total"],
            "passed": v["passed"],
            "accuracy": round(v["passed"] / v["total"], 4) if v["total"] > 0 else 0.0,
        }
        for k, v in cat_stats.items()
    }

    return RoutingEvalResult(
        evaluated_at=now_iso,
        total=total,
        passed=passed,
        accuracy=accuracy,
        mode="full",
        categories=categories_out,
        failures=failures,
    )


def save_eval_result(result: RoutingEvalResult, path: Path | None = None) -> Path:
    """Persist evaluation result as JSON for Board display and trend tracking."""
    target_path = path or default_eval_result_path()
    target_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(result.to_dict(), indent=2)
    target_path.write_text(payload, encoding="utf-8")
    return target_path


def get_latest_routing_eval(path: Path | None = None) -> dict[str, Any] | None:
    """Read the latest persisted routing evaluation result if available."""
    target_path = path or default_eval_result_path()
    if not target_path.exists():
        return None
    try:
        data = json.loads(target_path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception as exc:  # noqa: BLE001
        log.warning("Failed to load latest routing eval result: %s", exc)
        return None


def extract_candidate_cases_from_feedback(
    store: ConversationStore | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Extract candidate evaluation cases from SC-C2 routing override feedback."""
    s = store or get_conversation_store()
    router = BarbRouter()
    feedbacks = router.list_routing_feedback(store=s, limit=limit)

    candidates: list[dict[str, Any]] = []
    for fb in feedbacks:
        candidates.append(
            {
                "prompt": fb.prompt,
                "category": "override_feedback",
                "expected_role": fb.override_role,
                "expected_roles": [fb.override_role],
                "original_role": fb.original_role,
                "reason": fb.reason,
                "overridden_by": fb.overridden_by,
                "created_at": fb.created_at,
            }
        )
    return candidates
