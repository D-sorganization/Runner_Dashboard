"""Barb routing evaluation set and regression check (SC-C7, Issue #1340).

The benchmark cases live beside this module (``routing_eval_cases.json``) so
every deployed node can run them. Two judges share one scorer:

* ``evaluate_deterministic`` checks the Stage 1 pre-router only. CI runs it on
  every change (``tests/staff/routing_eval``) and requires every case to pass.
* ``evaluate_full`` checks the two-stage ``BarbRouter``. The backend refreshes
  it daily (``routing_eval_loop``) and the Board shows the latest summary.

Routing overrides recorded by SC-C2 become candidate cases through
``extract_candidate_cases_from_feedback``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from staff.conversations import ConversationStore, get_conversation_store
from staff.router import BarbRouter, route_deterministic
from staff.router_models import RoutingDecision

log = logging.getLogger("dashboard.staff.routing_eval")

DEFAULT_CASES_PATH = Path(__file__).resolve().with_name("routing_eval_cases.json")

#: Seconds between background refreshes of the full-router eval (daily).
ROUTING_EVAL_INTERVAL_S = 24 * 60 * 60


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
        if self.expected_clarify == bool(self.expected_roles):
            raise ValueError(f"RoutingEvalCase({self.id}) must expect either clarification or a role, not both")


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


#: A judge returns ``None`` when the case passes, else the failure reason.
Judge = Callable[[RoutingEvalCase], str | None]


def _judge_decision(case: RoutingEvalCase, decision: RoutingDecision) -> str | None:
    """Compare one routed (non-clarify) decision with the case's expectations."""
    diffs: list[str] = []
    if decision.chosen_role not in case.expected_roles:
        diffs.append(f"role '{decision.chosen_role}' not in {case.expected_roles}")
    if case.is_code_change and not decision.is_code_change:
        diffs.append("is_code_change False, expected True")
    if case.expected_answer_myself and decision.chosen_role != "barb":
        diffs.append("expected barb self-handling")
    return "; ".join(diffs) or None


def _judge_deterministic(case: RoutingEvalCase) -> str | None:
    """Stage 1 must defer ambiguous prompts (``None``) and route the rest."""
    decision = route_deterministic(case.prompt)
    if case.expected_clarify:
        if decision is None:
            return None
        return f"Expected deferral (None) for ambiguous prompt, got role '{decision.chosen_role}'"
    if decision is None:
        return f"Pre-router returned None, expected '{case.expected_role}'"
    return _judge_decision(case, decision)


def _full_judge(router: BarbRouter) -> Judge:
    """The two-stage router must ask for clarification on ambiguous prompts."""

    def judge(case: RoutingEvalCase) -> str | None:
        decision = router.route(case.prompt)
        if case.expected_clarify:
            if decision.needs_clarification or decision.chosen_role is None:
                return None
            return f"Expected clarification, got role '{decision.chosen_role}'"
        return _judge_decision(case, decision)

    return judge


def _score(cases: list[RoutingEvalCase], judge: Judge, mode: str) -> RoutingEvalResult:
    """Run ``judge`` over ``cases`` and tally accuracy overall and per category.

    Postcondition: ``passed + len(failures) == total``.
    """
    if not cases:
        raise ValueError("routing eval needs at least one case")
    failures: list[dict[str, Any]] = []
    tallies: dict[str, dict[str, int]] = {}
    for case in cases:
        tally = tallies.setdefault(case.category, {"total": 0, "passed": 0})
        tally["total"] += 1
        reason = judge(case)
        if reason is None:
            tally["passed"] += 1
        else:
            failures.append({"id": case.id, "prompt": case.prompt, "reason": reason})

    total = len(cases)
    passed = total - len(failures)
    assert passed == sum(t["passed"] for t in tallies.values())
    return RoutingEvalResult(
        evaluated_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        total=total,
        passed=passed,
        accuracy=passed / total,
        mode=mode,
        categories={name: {**t, "accuracy": round(t["passed"] / t["total"], 4)} for name, t in tallies.items()},
        failures=failures,
    )


def evaluate_deterministic(cases: list[RoutingEvalCase]) -> RoutingEvalResult:
    """Evaluate the deterministic pre-router (Stage 1) against ``cases``."""
    return _score(cases, _judge_deterministic, "deterministic")


def evaluate_full(cases: list[RoutingEvalCase], router: BarbRouter | None = None) -> RoutingEvalResult:
    """Evaluate the full two-stage router against ``cases``."""
    return _score(cases, _full_judge(router or BarbRouter()), "full")


def save_eval_result(result: RoutingEvalResult, path: Path | None = None) -> Path:
    """Persist evaluation result as JSON for Board display and trend tracking."""
    target_path = path or default_eval_result_path()
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(json.dumps(result.to_dict(), indent=2), encoding="utf-8")
    return target_path


def get_latest_routing_eval(path: Path | None = None) -> dict[str, Any] | None:
    """Read the latest persisted routing evaluation result, or ``None`` if absent.

    An unreadable or malformed file is logged and treated as absent so the
    Board keeps rendering; the next refresh overwrites it.
    """
    target_path = path or default_eval_result_path()
    if not target_path.exists():
        return None
    try:
        data = json.loads(target_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("routing eval: cannot read %s: %s", target_path, exc)
        return None
    if not isinstance(data, dict):
        log.warning("routing eval: %s does not hold a JSON object", target_path)
        return None
    return data


def refresh_routing_eval(path: Path | None = None, cases_path: Path | None = None) -> RoutingEvalResult:
    """Run the full-router eval once and persist it for the Board.

    Postcondition: the result file exists and holds this run.
    """
    result = evaluate_full(load_eval_cases(cases_path))
    saved = save_eval_result(result, path=path)
    assert saved.exists()
    log.info("routing eval: %d/%d passed (%.1f%%)", result.passed, result.total, result.accuracy * 100)
    return result


async def routing_eval_loop(interval_s: int = ROUTING_EVAL_INTERVAL_S) -> None:
    """Background task: refresh the routing eval at startup, then every ``interval_s``.

    Precondition: ``interval_s >= 60``. A failed run is logged and retried on
    the next tick; it never stops the loop or the server.
    """
    assert interval_s >= 60, "routing eval interval must be >= 60 s"
    while True:
        try:
            await asyncio.to_thread(refresh_routing_eval)
        except Exception:  # noqa: BLE001 - a bad run must not kill the loop
            log.exception("routing eval: refresh failed")
        await asyncio.sleep(interval_s)


def extract_candidate_cases_from_feedback(
    store: ConversationStore | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Extract candidate evaluation cases from SC-C2 routing override feedback."""
    s = store or get_conversation_store()
    feedbacks = BarbRouter().list_routing_feedback(store=s, limit=limit)
    return [
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
        for fb in feedbacks
    ]
