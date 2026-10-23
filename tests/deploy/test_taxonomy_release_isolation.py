"""Issue-label events must not cancel rollouts or poison release check snapshots."""

import ast
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/taxonomy-rollout.yml"
SHA = "a" * 40


def _expression(source: str, context: dict[str, Any]) -> Any:
    """Evaluate the boolean/attribute subset used by GitHub workflow conditions."""
    source = source.removeprefix("${{").removesuffix("}}").strip()
    tree = ast.parse(source.replace("&&", " and ").replace("||", " or "), mode="eval")

    def visit(node: ast.AST) -> Any:
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            return context[node.id]
        if isinstance(node, ast.Attribute):
            return visit(node.value)[node.attr]
        if isinstance(node, ast.BoolOp):
            for child in node.values:
                value = visit(child)
                if isinstance(node.op, ast.And) and not value or isinstance(node.op, ast.Or) and value:
                    return value
            return value
        if isinstance(node, ast.Compare) and len(node.ops) == 1 and isinstance(node.ops[0], ast.Eq):
            left, right = visit(node.left), visit(node.comparators[0])
            return (
                left.casefold() == right.casefold()
                if isinstance(left, str) and isinstance(right, str)
                else left == right
            )
        raise AssertionError(f"unsupported workflow expression node: {type(node).__name__}")

    return visit(tree.body)


def _event(name: str, label: str = "", run_id: int = 1) -> dict[str, Any]:
    return {"github": {"event_name": name, "event": {"label": {"name": label}}, "run_id": run_id}}


def _route_runs(context: dict[str, Any]) -> bool:
    config = yaml.safe_load(WORKFLOW.read_text())
    condition = config["jobs"]["route"].get("if")
    return True if condition is None else bool(_expression(condition, context))


def _group(context: dict[str, Any]) -> str:
    group = yaml.safe_load(WORKFLOW.read_text())["concurrency"]["group"]
    return re.sub(r"\$\{\{(.+?)\}\}", lambda match: str(_expression(match[1], context)), group)


@pytest.mark.parametrize("label", ["claim:codex", "bug", "rollout:done"])
def test_ordinary_labels_skip_every_taxonomy_job(label: str) -> None:
    assert not _route_runs(_event("issues", label))


@pytest.mark.parametrize(
    "name,label", [("workflow_dispatch", ""), ("issues", "rollout:taxonomy"), ("issues", "rollout:taxonomy-apply")]
)
def test_requested_rollouts_still_run(name: str, label: str) -> None:
    assert _route_runs(_event(name, label))


def test_ordinary_labels_cannot_cancel_a_requested_rollout() -> None:
    requested = _group(_event("issues", "rollout:taxonomy"))
    assert _group(_event("workflow_dispatch")) == requested
    assert _group(_event("issues", "rollout:taxonomy-apply")) == requested
    ignored = _group(_event("issues", "claim:codex", run_id=2))
    assert ignored != requested
    assert ignored != _group(_event("issues", "bug", run_id=3))


@pytest.mark.parametrize("source_failure", [False, True])
def test_label_event_snapshot_preserves_release_gate_semantics(
    source_failure: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.syspath_prepend(str(ROOT / "deploy"))
    from main_auto_release.gate import commit_passes_publisher_ci_gate

    # Actual incident: unrelated claim events produced cancelled route/preview/apply
    # checks on otherwise green main. A skipped route also skips its dependent jobs.
    conclusion = "cancelled" if _route_runs(_event("issues", "claim:codex")) else "skipped"
    checks = [
        {"name": name, "head_sha": SHA, "status": "completed", "conclusion": conclusion}
        for name in ("route", "preview", "apply")
    ]
    checks += [
        {
            "name": "quality-gate",
            "head_sha": SHA,
            "status": "completed",
            "conclusion": "failure" if source_failure else "success",
        },
        {"name": "tests", "head_sha": SHA, "status": "completed", "conclusion": "success"},
    ]
    assert commit_passes_publisher_ci_gate(checks, SHA)[0] is not source_failure
