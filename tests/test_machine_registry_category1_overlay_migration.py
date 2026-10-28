"""Category 1 of #3082: host/tailnet names and machine inventory move behind the overlay.

The public repo ships no live fleet topology in ``backend/machine_registry.yml``;
real hosts live in a local file or the private overlay, per
docs/runbooks/local-fleet-topology.md.
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_BACKEND = _ROOT / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))
_SCRIPTS = _ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import sanitize_private_topology as spt  # noqa: E402
from machine_registry import DEFAULT_REGISTRY_PATH, load_machine_registry  # noqa: E402

# Runbooks that previously documented real host/tailnet topology for category 1.
# docs/runbooks/brick-dashboard-redeploy.md is gone: sanitizing it made it a
# byte-for-byte duplicate of the already-public node-c-dashboard-redeploy.md,
# so it was removed rather than kept as a second copy (DRY).
CATEGORY_1_RUNBOOKS = (
    "docs/runbooks/node-c-dashboard-redeploy.md",
    "docs/runbooks/oglaptop-production-oauth.md",
    "docs/runbooks/fleet-hub-deskcomputer-rollout-2022.md",
)

# Single source of truth for the real literals (DRY): same mapping the sanitizer
# script applies. A real literal appearing in a tracked file means the migration
# regressed, without this test ever hard-coding the value itself.
_REAL_LITERALS = tuple(old for old, _new in spt.LITERAL_REPLACEMENTS)


def test_shipped_machine_registry_has_no_live_machines() -> None:
    registry = load_machine_registry(DEFAULT_REGISTRY_PATH)
    assert registry["machines"] == []


def test_category1_runbooks_contain_no_real_fleet_literals() -> None:
    for rel_path in CATEGORY_1_RUNBOOKS:
        text = (_ROOT / rel_path).read_text(encoding="utf-8")
        for literal in _REAL_LITERALS:
            assert literal not in text, f"{rel_path} still contains a real fleet literal"
