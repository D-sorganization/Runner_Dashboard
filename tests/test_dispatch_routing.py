"""Unit tests for tier-based model routing and per-repo concurrency (RD-3, issue #1848).

Acceptance criteria:
- The dispatch audit shows the tier, model and reason for each run.
- A second dispatch with overlapping paths waits in the queue until the first PR merges or is closed.
"""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from dispatch_routing import (
    DispatchQueueManager,
    ModelRoutingDecision,
    derive_tier,
    extract_declared_paths,
    is_dictated_fix,
    paths_overlap,
    record_dispatch_routing_audit,
    resolve_model_routing,
)

# ─── 1. Tier derivation tests ────────────────────────────────────────────────


def test_derive_tier_explicit_label() -> None:
    assert derive_tier(["tier:cli", "enhancement"]) == "cli"
    assert derive_tier(["tier:strong"]) == "strong"
    assert derive_tier(["tier:ollama"]) == "ollama"


def test_derive_tier_taxonomy_strong_signals() -> None:
    assert derive_tier(["complexity:complex"]) == "strong"
    assert derive_tier(["complexity:deep"]) == "strong"
    assert derive_tier(["complexity:research"]) == "strong"
    assert derive_tier(["judgement:design"]) == "strong"
    assert derive_tier(["judgement:contested"]) == "strong"
    assert derive_tier(["type:epic"]) == "strong"
    assert derive_tier(["security"]) == "strong"
    assert derive_tier(["panel-review"]) == "strong"


def test_derive_tier_taxonomy_cli_signals() -> None:
    assert derive_tier(["complexity:trivial"]) == "cli"
    assert derive_tier(["complexity:simple"]) == "cli"
    assert derive_tier(["complexity:routine"]) == "cli"
    assert derive_tier(["complexity:small"]) == "cli"
    assert derive_tier(["complexity:medium"]) == "cli"


def test_derive_tier_unclassified_failsafe_strong() -> None:
    assert derive_tier([]) == "strong"
    assert derive_tier(["bug", "backend"]) == "strong"


# ─── 2. Dictated fix detection tests ──────────────────────────────────────────


def test_is_dictated_fix_positive_cases() -> None:
    # Exact replacement / math fix (from issue description)
    p1 = "Generate edit values that can never equal baseline: 701.0 + float(count % 79)"
    assert is_dictated_fix(p1) is True

    # Dockerfile / single-line variable update
    p2 = "Update Dockerfile: change ARG NODE_MAJOR=20 to ARG NODE_MAJOR=22"
    assert is_dictated_fix(p2) is True

    # Replacement syntax
    p3 = "In _includes/mathjax-loader.html, replace `tags: 'ams'` with `tags: 'none'`"
    assert is_dictated_fix(p3) is True

    # Diff patch chunk
    p4 = "Apply fix:\n--- a/file.py\n+++ b/file.py\n@@ -1,2 +1,2 @@\n-old\n+new"
    assert is_dictated_fix(p4) is True


def test_is_dictated_fix_negative_cases() -> None:
    p1 = "Implement tier-based model routing and per-repo concurrency limits at dispatch"
    assert is_dictated_fix(p1) is False

    p2 = "Refactor the authentication middleware to support JWT session tokens"
    assert is_dictated_fix(p2) is False


# ─── 3. Model routing decision tests ──────────────────────────────────────────


def test_resolve_model_routing_dictated_fix() -> None:
    prompt = "In src/glass_models/axi/coupled.py, change `count % 80` to `701.0 + float(count % 79)`"
    decision = resolve_model_routing(labels=["tier:cli"], prompt=prompt)
    assert isinstance(decision, ModelRoutingDecision)
    assert decision.tier == "cli"
    assert "haiku" in decision.model.lower()
    assert decision.is_dictated_fix is True
    assert "dictated" in decision.reason.lower()


def test_resolve_model_routing_well_specified_implementation() -> None:
    prompt = "Implement pre-dispatch premise check in backend/dispatch_premise.py following SPEC.md"
    decision = resolve_model_routing(labels=["tier:cli"], prompt=prompt)
    assert decision.tier == "cli"
    assert "sonnet" in decision.model.lower()
    assert decision.is_dictated_fix is False
    assert "well-specified" in decision.reason.lower() or "cli" in decision.reason.lower()


def test_resolve_model_routing_strong_tier() -> None:
    prompt = "Architect cross-repo consensus debate system between 3-4 experts"
    decision = resolve_model_routing(labels=["type:epic", "judgement:design"], prompt=prompt)
    assert decision.tier == "strong"
    assert "opus" in decision.model.lower()
    assert "strong" in decision.reason.lower() or "design" in decision.reason.lower()


def test_resolve_model_routing_explicit_model_override() -> None:
    decision = resolve_model_routing(
        labels=["tier:cli"],
        prompt="Simple task",
        requested_model="custom-experimental-model",
    )
    assert decision.model == "custom-experimental-model"
    assert "explicit" in decision.reason.lower()


# ─── 4. Declared paths extraction and overlap tests ───────────────────────────


def test_extract_declared_paths_from_prompt() -> None:
    prompt = "Touch `backend/dispatch.py` and `docs/specs/ROUTING.md` to update config."
    paths = extract_declared_paths(prompt)
    assert "backend/dispatch.py" in paths
    assert "docs/specs/ROUTING.md" in paths


def test_extract_declared_paths_fallback() -> None:
    prompt = "Do general maintenance"
    paths = extract_declared_paths(prompt)
    assert paths == ["*"]


def test_paths_overlap_exact_and_directory() -> None:
    assert paths_overlap(["backend/server.py"], ["backend/server.py"]) is True
    assert paths_overlap(["backend/"], ["backend/server.py"]) is True
    assert paths_overlap(["backend/server.py"], ["backend/"]) is True
    assert paths_overlap(["backend/server.py"], ["frontend/src/App.tsx"]) is False
    assert paths_overlap(["*"], ["frontend/src/App.tsx"]) is True


# ─── 5. Per-repo concurrency and queueing tests ───────────────────────────────


def test_concurrency_queue_lifecycle() -> None:
    manager = DispatchQueueManager()

    # 1. First dispatch on repo1 with docs/ file -> allowed
    d1 = manager.try_dispatch(
        repository="D-sorganization/Runner_Dashboard",
        number=1612,
        prompt="Update `docs/operations/runbook.md`",
        labels=["tier:cli"],
    )
    assert d1.dispatched is True
    assert d1.session is not None
    assert d1.queued_item is None

    # 2. Second dispatch on same repo with overlapping path -> queued!
    d2 = manager.try_dispatch(
        repository="D-sorganization/Runner_Dashboard",
        number=1614,
        prompt="Also update `docs/operations/runbook.md` with new notes",
        labels=["tier:cli"],
    )
    assert d2.dispatched is False
    assert d2.session is None
    assert d2.queued_item is not None
    assert d2.queued_item.repository == "D-sorganization/Runner_Dashboard"
    assert d2.queued_item.number == 1614
    assert d2.queued_item.blocking_session_id == d1.session.session_id

    # 3. Third dispatch on same repo with NON-overlapping paths -> allowed!
    d3 = manager.try_dispatch(
        repository="D-sorganization/Runner_Dashboard",
        number=1700,
        prompt="Fix `frontend/src/components/Badge.tsx`",
        labels=["tier:cli"],
    )
    assert d3.dispatched is True
    assert d3.session is not None

    # 4. Another repo dispatch with same path -> allowed (isolated per repo)
    d4 = manager.try_dispatch(
        repository="D-sorganization/Tools",
        number=5000,
        prompt="Update `docs/operations/runbook.md`",
        labels=["tier:cli"],
    )
    assert d4.dispatched is True

    # 5. First PR merges or closes -> release session and dequeue d2!
    dequeued = manager.release_session(d1.session.session_id, outcome="merged")
    assert len(dequeued) == 1
    assert dequeued[0].number == 1614

    # Now d2 can be dispatched
    d2_retry = manager.try_dispatch(
        repository="D-sorganization/Runner_Dashboard",
        number=1614,
        prompt="Also update `docs/operations/runbook.md` with new notes",
        labels=["tier:cli"],
    )
    assert d2_retry.dispatched is True


def test_concurrency_queue_pr_callback() -> None:
    manager = DispatchQueueManager()

    # Dispatch session and associate PR #1856
    d1 = manager.try_dispatch(
        repository="D-sorganization/Runner_Dashboard",
        number=1847,
        prompt="Edit `backend/dispatch_premise.py`",
        labels=["tier:cli"],
    )
    assert d1.dispatched is True
    manager.attach_pr(d1.session.session_id, pr_number=1856)

    # Overlapping second dispatch
    d2 = manager.try_dispatch(
        repository="D-sorganization/Runner_Dashboard",
        number=1848,
        prompt="Edit `backend/dispatch_premise.py` and `backend/dispatch_routing.py`",
        labels=["tier:cli"],
    )
    assert d2.dispatched is False

    # Simulate PR #1856 merged
    dequeued = manager.on_pr_merged_or_closed(
        repository="D-sorganization/Runner_Dashboard",
        pr_number=1856,
        outcome="merged",
    )
    assert len(dequeued) == 1
    assert dequeued[0].number == 1848


# ─── 6. Dispatch audit logging tests ──────────────────────────────────────────


def test_record_dispatch_routing_audit() -> None:
    with TemporaryDirectory() as tmpdir:
        audit_file = Path(tmpdir) / "dispatch_audit.ndjson"

        decision = ModelRoutingDecision(
            tier="cli",
            model="haiku-4-5",
            reason="dictated_fix: prompt contains exact replacement",
            is_dictated_fix=True,
        )

        record_dispatch_routing_audit(
            audit_file=audit_file,
            repository="D-sorganization/Tools_Private",
            number=1924,
            decision=decision,
            status="dispatched",
            declared_paths=["src/glass_models/explorer/tests/test_session_snapshots.py"],
            principal="dieterolson",
        )

        assert audit_file.exists()
        lines = audit_file.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        entry = json.loads(lines[0])

        assert entry["event_type"] == "dispatch_routing"
        assert entry["repository"] == "D-sorganization/Tools_Private"
        assert entry["number"] == 1924
        assert entry["tier"] == "cli"
        assert entry["model"] == "haiku-4-5"
        assert "dictated_fix" in entry["reason"]
        assert entry["status"] == "dispatched"
        assert "recorded_at" in entry
