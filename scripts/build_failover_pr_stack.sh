#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
FULL="${1:?full commit sha}"
MAIN="${2:-origin/main}"

checkout_slice() {
  local dest="$1"
  shift
  git checkout -B "$dest" "$MAIN"
  for f in "$@"; do
    git checkout "$FULL" -- "$f"
  done
}

# ── Slice 1 ──────────────────────────────────────────────────────────────────
checkout_slice cursor/staff-failover-1-classify-28ed \
  backend/staff/adapter_failure.py \
  backend/staff/classifier.py \
  tests/unit/test_staff_adapter_classification.py \
  tests/unit/test_staff_classifier.py \
  changes/2047-fleet-wide-staff-provider-failover-chain.md

python3 <<'PY'
from pathlib import Path
for rel in (
    "backend/staff/classifier.py",
    "tests/unit/test_staff_classifier.py",
):
    p = Path(rel)
    p.write_text(p.read_text().replace('        "providers_exhausted",\n', ""))
PY

git add -A
git commit -m "feat(staff): per-adapter failure classification (#2047 slice 1)"

# ── Slice 2 ──────────────────────────────────────────────────────────────────
git checkout -B cursor/staff-failover-2-cooldown-28ed
git checkout "$FULL" -- \
  backend/staff/provider_cooldown.py \
  backend/staff/fleet.py \
  backend/staff/models.py \
  backend/staff/summary_view.py

cat > tests/unit/test_staff_provider_cooldown.py <<'EOF'
"""Provider cooldown store and summary exposure (#2047 slice 2)."""

from __future__ import annotations

import json
import time

import pytest
from staff.provider_cooldown import (
    cooldown_snapshot,
    mark_provider_cooldown,
    provider_in_cooldown,
    reset_provider_cooldowns,
)


@pytest.fixture(autouse=True)
def _reset() -> None:
    reset_provider_cooldowns()


def test_cooldown_expires() -> None:
    mark_provider_cooldown("codex", "provider_capacity", message="retry after 1 seconds")
    assert provider_in_cooldown("codex")
    time.sleep(1.1)
    assert not provider_in_cooldown("codex")


def test_cooldown_snapshot_serializable() -> None:
    mark_provider_cooldown("claude", "quota_exhausted")
    snap = cooldown_snapshot()
    assert snap and snap[0]["provider"] == "claude"
    json.dumps(snap)
EOF

git add tests/unit/test_staff_provider_cooldown.py
git commit -m "feat(staff): provider cooldown store and summary field (#2047 slice 2)"

# ── Slice 3 ──────────────────────────────────────────────────────────────────
git checkout -B cursor/staff-failover-3-chain-28ed
git checkout "$FULL" -- \
  backend/staff/provider_failover_config.py \
  config/provider_failover.json \
  backend/staff/failover_dispatch.py \
  backend/staff/failover_model.py \
  backend/staff/model_tiers.py \
  backend/staff/model_resolution.py \
  backend/staff/runner.py \
  backend/staff/plan.py \
  backend/staff/provider_fallback.py \
  backend/staff/runner_ops.py \
  config/staff_model_tiers.json \
  tests/unit/test_staff_model_resolution.py

git checkout "$FULL" -- tests/unit/test_staff_provider_failover_fleet.py
python3 <<'PY'
from pathlib import Path
lines = Path("tests/unit/test_staff_provider_failover_fleet.py").read_text().splitlines()
skip_names = {
    "test_chain_failover_schedules_next_provider",
    "test_providers_exhausted_attention_state",
    "test_tried_providers_includes_retry_attempts",
}
out, skip = [], False
for line in lines:
    if line.startswith("def test_"):
        name = line.split("(")[0].replace("def ", "")
        skip = name in skip_names
    if skip:
        continue
    out.append(line)
Path("tests/unit/test_staff_provider_failover_chain.py").write_text("\n".join(out) + "\n")
Path("tests/unit/test_staff_provider_failover_fleet.py").unlink()
PY

git add -A
git commit -m "feat(staff): default failover chain and pre-launch provider pick (#2047 slice 3)"

# ── Slice 4 ──────────────────────────────────────────────────────────────────
git checkout -B cursor/staff-failover-4-crossmachine-28ed
git checkout "$FULL" -- \
  backend/staff/classifier.py \
  backend/staff/dispatch_service.py \
  backend/staff/retry.py \
  backend/staff/run_schema.py \
  backend/staff/store.py \
  tests/unit/test_staff_classifier.py \
  tests/unit/test_staff_provider_failover_fleet.py \
  docs/staff-hub.md

git add -A
git commit -m "feat(staff): cross-machine placement and providers_exhausted (#2047 slice 4)"

# ── Slice 5 ──────────────────────────────────────────────────────────────────
git checkout -B cursor/staff-failover-5-tiers-28ed
git checkout "$FULL" -- \
  backend/agent_remediation/provider_registry.py \
  backend/staff/adapters.py \
  backend/staff/roles.py \
  backend/staff/schema.json \
  backend/staff/validator.py \
  tests/api/test_antigravity_provider.py \
  tests/unit/test_staff_model_tiers.py \
  tests/unit/test_staff_provider_fallback.py

git add -A
git commit -m "feat(staff): model tiers, escalation gate, DRY provider allowlists (#2047 slice 5)"

echo "Built stack; tip=$(git rev-parse --short HEAD)"
