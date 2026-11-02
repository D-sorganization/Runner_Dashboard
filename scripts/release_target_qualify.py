"""Read-only release qualification checks for dashboard worker nodes (#3596)."""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from typing import Any

_ROOT = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_ROOT)
_BACKEND = os.path.join(_REPO, "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from staff.adapters import get_adapter  # noqa: E402


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str = ""
    deferred: bool = False


@dataclass
class QualifyReport:
    hub_url: str
    checks: list[CheckResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "hub_url": self.hub_url,
            "checks": [asdict(c) for c in self.checks],
            "ready_for_upgrade": all(c.ok for c in self.checks if not c.deferred),
        }


def _get_json(url: str, token: str | None = None, timeout: float = 20.0) -> tuple[dict[str, Any] | list[Any] | None, str]:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers, method="GET")  # noqa: S310
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return None, f"HTTP {exc.code}"
    except urllib.error.URLError as exc:
        return None, str(exc.reason)
    except TimeoutError:
        return None, "timeout"
    try:
        return json.loads(body), ""
    except json.JSONDecodeError:
        return None, "invalid JSON"


def check_hub_health(hub_url: str, token: str | None) -> CheckResult:
    base = hub_url.rstrip("/")
    payload, err = _get_json(f"{base}/api/health", token)
    if payload is None:
        return CheckResult("hub_health", False, err or "unreachable")
    return CheckResult("hub_health", True, str(payload.get("status", "ok")))


def check_fleet_status(hub_url: str, token: str | None) -> CheckResult:
    base = hub_url.rstrip("/")
    payload, err = _get_json(f"{base}/api/fleet/status", token)
    if not isinstance(payload, dict):
        return CheckResult("fleet_status", False, err or "missing payload")
    deploy = payload.get("auto_deploy") or {}
    sha = deploy.get("deployed_sha") or deploy.get("sha") or ""
    return CheckResult("fleet_status", True, f"deployed_sha={sha or 'unknown'}")


def check_coordination_jobs(hub_url: str, token: str | None) -> CheckResult:
    base = hub_url.rstrip("/")
    payload, err = _get_json(f"{base}/api/coordination/jobs?repo=Runner_Dashboard&limit=5", token)
    if not isinstance(payload, dict):
        return CheckResult("coordination_jobs", False, err or "jobs unavailable")
    if payload.get("available") is False:
        return CheckResult("coordination_jobs", False, "holds or filters unavailable")
    return CheckResult("coordination_jobs", True, f"jobs={len(payload.get('jobs') or [])}")


def check_codex_chat_argv() -> CheckResult:
    argv = get_adapter("codex").chat_argv("probe", "/tmp/scratch", model="gpt-6-luna")
    problems: list[str] = []
    if "--session" in argv:
        problems.append("deprecated --session flag present")
    if "--json" not in argv:
        problems.append("missing --json")
    if argv[1:3] != ["exec", "--sandbox"]:
        problems.append(f"unexpected exec prefix: {argv[1:5]}")
    resume = get_adapter("codex").chat_argv("probe", "/tmp/scratch", session_id="sess", model="gpt-6-luna")
    if resume[1:3] != ["exec", "resume"]:
        problems.append("resume must use exec resume subcommand")
    if problems:
        return CheckResult(
            "codex_chat_argv",
            False,
            "; ".join(problems) + " (fix #3595)",
        )
    return CheckResult("codex_chat_argv", True, "exec/resume/json contract ok")


def deferred_live_codex_worker() -> CheckResult:
    return CheckResult(
        "live_codex_worker_task",
        True,
        "Requires qualified published release on the target node + dashboard-managed Codex run; record in deferred-validation catalog after deploy.",
        deferred=True,
    )


def run_qualify(hub_url: str, token: str | None = None) -> QualifyReport:
    report = QualifyReport(hub_url=hub_url)
    report.checks = [
        check_hub_health(hub_url, token),
        check_fleet_status(hub_url, token),
        check_coordination_jobs(hub_url, token),
        check_codex_chat_argv(),
        deferred_live_codex_worker(),
    ]
    return report


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Read-only RD release qualification on a dashboard worker node (#3596)")
    parser.add_argument("--hub-url", default=os.environ.get("FLEET_API_URL", "http://127.0.0.1:8321"))
    parser.add_argument("--token", default=os.environ.get("FLEET_API_TOKEN"))
    args = parser.parse_args(argv)
    report = run_qualify(args.hub_url, args.token or None)
    print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    blocking = [c for c in report.checks if not c.ok and not c.deferred]
    return 0 if not blocking else 1


if __name__ == "__main__":
    raise SystemExit(main())
