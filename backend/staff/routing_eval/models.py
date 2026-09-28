"""Barb routing evaluation models and contracts (SC-C7, Issue #1340)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class RoutingEvalCase:
    """A representative routing test case with expected outcomes."""

    id: str
    prompt: str
    expected_role: str | None
    expected_outcome: str  # "route", "answer_myself", "clarify"
    category: str
    deterministic: bool
    expected_is_code: bool = False
    tags: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RoutingEvalCase:
        return cls(
            id=str(data["id"]),
            prompt=str(data["prompt"]),
            expected_role=data.get("expected_role"),
            expected_outcome=str(data["expected_outcome"]),
            category=str(data["category"]),
            deterministic=bool(data["deterministic"]),
            expected_is_code=bool(data.get("expected_is_code", False)),
            tags=tuple(data.get("tags") or ()),
        )


@dataclass
class CaseEvalResult:
    """Individual test case evaluation outcome."""

    case_id: str
    prompt: str
    category: str
    expected_role: str | None
    actual_role: str | None
    expected_outcome: str
    actual_outcome: str
    passed: bool
    confidence: float
    mode: str
    reason: str
    latency_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EvalSummary:
    """Overall and category-level routing evaluation metrics."""

    total_cases: int
    passed_cases: int
    failed_cases: int
    accuracy: float
    deterministic_total: int
    deterministic_passed: int
    deterministic_accuracy: float
    category_metrics: dict[str, dict[str, Any]] = field(default_factory=dict)
    failures: list[dict[str, Any]] = field(default_factory=list)
    candidate_cases_count: int = 0
    timestamp: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_markdown(self) -> str:
        """Format an executive markdown table summary suitable for Board presentation."""
        lines = [
            "## Barb Routing Evaluation Report",
            f"**Timestamp:** `{self.timestamp}`  ",
            f"**Overall Accuracy:** `{self.accuracy:.1%}` ({self.passed_cases}/{self.total_cases})  ",
            (
                f"**Deterministic Pre-Router Accuracy:** `{self.deterministic_accuracy:.1%}` "
                f"({self.deterministic_passed}/{self.deterministic_total})  "
            ),
            f"**Candidate Overrides Evaluated:** `{self.candidate_cases_count}`",
            "",
            "### Category Performance Breakdown",
            "| Category | Total | Passed | Accuracy |",
            "| :--- | :---: | :---: | :---: |",
        ]
        for cat, met in sorted(self.category_metrics.items()):
            acc_str = f"{met['accuracy']:.1%}" if met["total"] > 0 else "N/A"
            lines.append(f"| `{cat}` | {met['total']} | {met['passed']} | {acc_str} |")

        if self.failures:
            lines.extend(
                [
                    "",
                    "### Failure Diagnostics",
                    "| ID | Prompt | Expected | Actual | Mode |",
                    "| :--- | :--- | :--- | :--- | :--- |",
                ]
            )
            for f in self.failures[:10]:
                prompt_snip = f"{f['prompt'][:32]}..."
                lines.append(
                    f"| `{f['case_id']}` | {prompt_snip} | `{f['expected_role']}` | "
                    f"`{f['actual_role']}` | `{f['mode']}` |"
                )

        return "\n".join(lines)
