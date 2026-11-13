"""Fork-PR / self-hosted workflow canary (#3080, docs/architecture/publish-guard.md)."""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from publish_guard import MODES, Finding, resolve_mode  # noqa: E402

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"
HOSTED = re.compile(r"^(ubuntu|windows|macos)-[\w.-]+$")
FORK_GUARD = re.compile(
    r"head\.repo\.full_name\s*==\s*github\.repository"
    r"|head\.repo\.fork\s*==\s*false"
    r"|!\s*github\.event\.pull_request\.head\.repo\.fork"
    # Same-repo bot authors only (a fork PR can never be authored by them).
    r"|(?:pull_request\.user\.login|github\.actor)\s*==\s*'dependabot\[bot\]'"
)
FORK_ROUTE = re.compile(r"head\.repo\.full_name\s*!=\s*github\.repository\s*&&\s*'(?P<label>[^']+)'")
PR_CODE_REF = re.compile(r"pull_request\.head\.(sha|ref)|github\.head_ref|refs/pull/")


def _triggers(doc: dict[Any, Any]) -> set[str]:
    on = doc.get("on", doc.get(True))  # YAML 1.1 parses a bare `on:` key as True
    return {on} if isinstance(on, str) else set(on or ())


def _labels(runs_on: object) -> list[str]:
    if isinstance(runs_on, dict):
        return ["<group>"] if "group" in runs_on else [str(x) for x in _as_list(runs_on.get("labels"))]
    text = " ".join(str(x) for x in _as_list(runs_on))
    return re.findall(r"'([^']+)'", text) if "${{" in text else text.split()


def _as_list(value: object) -> list[object]:
    return value if isinstance(value, list) else [] if value is None else [value]


def _may_self_host(runs_on: object) -> bool:
    return any(not HOSTED.match(label) for label in _labels(runs_on))


def _routes_forks_to_hosted(runs_on: object) -> bool:
    m = FORK_ROUTE.search(str(runs_on))
    return m is not None and bool(HOSTED.match(m.group("label")))


def check_workflow(name: str, doc: dict[Any, Any]) -> list[Finding]:
    triggers = _triggers(doc)
    out: list[Finding] = []
    for job_id, job in (doc.get("jobs") or {}).items():
        if not isinstance(job, dict):
            continue
        runs_on = job.get("runs-on", "")
        if triggers & {"pull_request", "pull_request_target"} and _may_self_host(runs_on):
            guarded = bool(FORK_GUARD.search(str(job.get("if", "")))) or _routes_forks_to_hosted(runs_on)
            if not guarded and "environment" not in job:
                out.append(Finding(f"{name}:{job_id}", "fork-pr-self-hosted"))
        if "pull_request_target" in triggers:
            for step in job.get("steps") or []:
                uses = str(step.get("uses", "")) if isinstance(step, dict) else ""
                if uses.startswith("actions/checkout") and PR_CODE_REF.search(str(step.get("with", {}))):
                    out.append(Finding(f"{name}:{job_id}", "prt-checkout-pr-code"))
    return out


def check_dir(workflows: Path = WORKFLOWS) -> list[Finding]:
    files = sorted([*workflows.glob("*.yml"), *workflows.glob("*.yaml")])
    if not files:
        raise SystemExit("workflow canary: no workflow files found")
    out: list[Finding] = []
    for f in files:
        out += check_workflow(f.name, yaml.safe_load(f.read_text(encoding="utf-8")) or {})
    return out


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--mode", choices=MODES)
    ap.add_argument("--workflows", type=Path, default=WORKFLOWS)
    args = ap.parse_args(argv)
    mode = resolve_mode(args.mode)
    violations = check_dir(args.workflows)
    for v in violations:
        print(v.render())
    print(f"workflow canary ({mode}): {len(violations)} violation(s)")
    return 1 if violations and mode == "enforce" else 0


if __name__ == "__main__":
    sys.exit(main())
