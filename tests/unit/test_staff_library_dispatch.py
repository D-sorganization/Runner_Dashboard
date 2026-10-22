from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from staff.plan import RunPlan, RunRequest  # noqa: E402
from staff.roles import RoleSpec  # noqa: E402
from staff.runner_ops import new_run_record  # noqa: E402
from staff.workspace import compose_prompt  # noqa: E402


def test_compose_prompt_includes_library_block() -> None:
    role = RoleSpec(name="research", title="Research", permissions={"library_references": True})
    block = "Library references (Alexandria):\n- Smith (2020), Book, p. 1 (work_id=1)"
    prompt = compose_prompt(
        role,
        repo="UpstreamDrift",
        target_ref="#1",
        operator_prompt="golf torque",
        branch="staff/research-1-abc",
        library_references=block,
    )
    assert block in prompt


def test_new_run_record_stores_library_work_ids() -> None:
    plan = RunPlan(
        role="r",
        provider="claude",
        model=None,
        repo="X",
        target_kind="prompt",
        target_ref="",
        operator_prompt="",
        prompt="p",
        argv=[],
        branch="b",
        lease_ritual=False,
        library_work_ids=(3, 7),
    )
    rec = new_run_record(plan, RunRequest(role="r"), "local", 2, None)
    assert json.loads(rec.library_work_ids) == [3, 7]
