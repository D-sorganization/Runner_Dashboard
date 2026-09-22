"""Usage ledger glue (issue #1200): finalise per-run cost, summarise, export to RM.

* ``finalize_cost`` runs once when a run ends. CLI-reported cost wins
  (``reported``); otherwise the price table or wall time fills ``cost_usd``.
* ``summary`` shapes ``RunStore.usage_by`` rows for the API with totals and the
  daily budget from ``STAFF_BUDGET_USD_PER_DAY``.
* ``export_to_rm`` appends today's per-provider totals to
  Repository_Management ``data/credit_usage.json`` by running that repo's
  ``scripts/append_credit_usage.py`` as a subprocess (no cross-repo import).
"""

from __future__ import annotations

import logging
import os
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from staff import workspace
from staff.lease import _python_for_rm
from staff.pricing import estimate_cost
from staff.store import USAGE_GROUPS, RunStore

log = logging.getLogger("dashboard.staff.usage")

EXPORT_SCRIPT = Path("scripts") / "append_credit_usage.py"
EXPORT_SOURCE = "runner-dashboard-staff"
EXPORT_WORKFLOW_TYPE = "runner_dashboard_staff"
# Map provider ids onto the cost categories docs/agent-credit-budget.md already tracks.
COST_CATEGORY = {"claude": "claude_code"}

Runner = Callable[..., subprocess.CompletedProcess[str]]


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def wall_seconds(started_at: str | None, ended_at: str | None) -> float:
    start, end = _parse_iso(started_at), _parse_iso(ended_at)
    if start is None or end is None:
        return 0.0
    return max(0.0, (end - start).total_seconds())


def finalize_cost(store: RunStore, run_id: str, provider: str, model: str | None) -> str:
    """Fill ``cost_usd``/``cost_method`` for a finished run. Returns the method.

    Pre: the run row exists (a missing row returns ``"none"`` and logs).
    Post: ``cost_method`` is one of ``pricing.METHODS``; ``cost_usd`` is never
    lowered — a CLI-reported non-zero cost is kept and marked ``reported``.
    """
    rec = store.get_run(run_id)
    if rec is None:
        log.warning("finalize_cost: run %s not found", run_id)
        return "none"
    if rec.cost_usd > 0:
        if rec.cost_method != "reported":
            store.update_run(run_id, cost_method="reported")
        return "reported"
    usd, method = estimate_cost(
        provider,
        model,
        input_tokens=rec.input_tokens,
        output_tokens=rec.output_tokens,
        wall_seconds=wall_seconds(rec.started_at, rec.ended_at),
    )
    store.update_run(run_id, cost_usd=usd, cost_method=method)
    return method


def budget_per_day() -> float:
    """Daily USD ceiling from ``STAFF_BUDGET_USD_PER_DAY`` (0 = unlimited)."""
    try:
        return max(0.0, float(os.environ.get("STAFF_BUDGET_USD_PER_DAY", "0") or 0))
    except ValueError:
        return 0.0


def today_iso() -> str:
    return datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0).isoformat().replace("+00:00", "Z")


def summary(store: RunStore, group: str = "provider", since: str | None = None) -> dict[str, Any]:
    """Rows + totals + budget block for ``GET /api/staff/usage``."""
    assert group in USAGE_GROUPS, group  # noqa: S101
    rows = store.usage_by(group, since)
    totals = {
        "runs": sum(r["runs"] for r in rows),
        "cost_usd": round(sum(r["cost_usd"] for r in rows), 6),
        "input_tokens": sum(r["input_tokens"] for r in rows),
        "output_tokens": sum(r["output_tokens"] for r in rows),
        "wall_seconds": round(sum(r["wall_seconds"] for r in rows), 1),
    }
    ceiling = budget_per_day()
    spent_today = store.spend_since(today_iso())["total"]
    return {
        "group": group,
        "since": since,
        "rows": rows,
        "totals": totals,
        "budget": {
            "usd_per_day": ceiling,
            "spent_today_usd": spent_today,
            "percent_used": round(spent_today / ceiling * 100.0, 1) if ceiling > 0 else None,
        },
    }


def export_to_rm(
    store: RunStore,
    rm_root: Path | None = None,
    *,
    date: str | None = None,
    repo: str = "Runner_Dashboard",
    run: Runner | None = None,
) -> dict[str, Any]:
    """Append today's per-provider totals to RM ``data/credit_usage.json``.

    Pre: ``rm_root`` (or ``workspace.rm_root()``) contains ``EXPORT_SCRIPT``;
    raises ``FileNotFoundError`` otherwise so the router can answer 503.
    Post: one ``append_credit_usage.py --replace`` invocation per provider with
    runs today; the result lists each entry and the script's exit code.
    """
    root = rm_root or workspace.rm_root()
    if root is None or not (root / EXPORT_SCRIPT).is_file():
        raise FileNotFoundError("Repository_Management checkout with scripts/append_credit_usage.py not found")
    invoke = run or subprocess.run
    day = date or today_iso()[:10]
    rows = store.usage_by("provider", since=f"{day}T00:00:00Z")
    exported: list[dict[str, Any]] = []
    for row in rows:
        provider = row["key"]
        argv = [
            _python_for_rm(),
            str(root / EXPORT_SCRIPT),
            "--date",
            day,
            "--repo",
            repo,
            "--workflow-type",
            EXPORT_WORKFLOW_TYPE,
            "--estimated-tokens",
            str(row["input_tokens"] + row["output_tokens"]),
            "--duration-minutes",
            str(int(round(row["wall_seconds"] / 60.0))),
            "--cost-category",
            COST_CATEGORY.get(provider, provider),
            "--cost-usd",
            str(row["cost_usd"]),
            "--source",
            EXPORT_SOURCE,
            "--replace",
        ]
        proc = invoke(argv, cwd=str(root), capture_output=True, text=True, timeout=60, check=False)  # noqa: S603
        exported.append(
            {
                "provider": provider,
                "cost_category": COST_CATEGORY.get(provider, provider),
                "runs": row["runs"],
                "cost_usd": row["cost_usd"],
                "exit_code": proc.returncode,
                "output": (proc.stdout + proc.stderr).strip()[:500],
            }
        )
        if proc.returncode != 0:
            log.warning("credit usage export failed for %s: %s", provider, exported[-1]["output"])
    return {"date": day, "rm_root": str(root), "exported": exported, "ok": all(e["exit_code"] == 0 for e in exported)}
