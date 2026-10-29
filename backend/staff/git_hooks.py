"""Install and run the staff worktree's lightweight pre-push checks."""

from __future__ import annotations

import importlib.util
import logging
import os
import re
import shlex
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType

log = logging.getLogger(__name__)

_ISSUE_BRANCH = re.compile(r"^(?:issue[-_]?)?#?(\d+)(?=$|[-_])")
_ZERO_SHA = re.compile(r"^0+$")
_TRUTHY = frozenset({"1", "true", "yes", "on"})


def _load_push_hygiene() -> ModuleType | None:
    try:
        from shared_scripts import push_hygiene

        return push_hygiene
    except ImportError:
        candidate = Path(__file__).resolve().parents[2] / "shared_scripts" / "push_hygiene.py"
        if not candidate.is_file():
            return None
        spec = importlib.util.spec_from_file_location("_rd_push_hygiene", candidate)
        if spec is None or spec.loader is None:
            return None
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
        return mod


def _git(root: Path, *args: str, input_text: str | None = None) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        input=input_text,
        text=True,
        capture_output=True,
        check=True,
        timeout=60,
    )
    return result.stdout.strip()


def install_pre_push_hook(worktree: Path) -> Path:
    """Install a worktree-private hook. Requires a valid writable Git worktree."""
    root = worktree.resolve(strict=True)
    git_dir = Path(_git(root, "rev-parse", "--git-dir"))
    if not git_dir.is_absolute():
        git_dir = (root / git_dir).resolve()
    hooks_dir = git_dir / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    hook = hooks_dir / "pre-push"
    # Reference this dashboard's own copy, not the target worktree's (which only exists for Runner_Dashboard).
    hook_script = Path(__file__).resolve()
    script = (
        f"#!/usr/bin/env sh\nset -eu\nexec {shlex.quote(sys.executable or 'python3')} "
        f"{shlex.quote(str(hook_script))} pre-push\n"
    )
    hook.write_text(script, encoding="utf-8")
    hook.chmod(0o755)
    _git(root, "config", "--worktree", "core.hooksPath", str(hooks_dir))
    return hook


def _issue_number(branch: str) -> str | None:
    for segment in branch.split("/"):
        match = _ISSUE_BRANCH.match(segment)
        if match:
            return match.group(1)
    return None


def _changed_python_files(root: Path, base: str, head: str) -> list[str]:
    output = _git(root, "diff", "--name-only", "--diff-filter=ACMRT", f"{base}...{head}", "--", "*.py")
    return [line for line in output.splitlines() if line]


def _fragment_exists(root: Path, base: str, head: str, issue: str) -> bool:
    output = _git(root, "diff", "--name-only", f"{base}...{head}", "--", "changes")
    return any(
        Path(name).parent.as_posix() == "changes"
        and re.fullmatch(rf"{re.escape(issue)}-[a-z0-9][a-z0-9-]*\.md", Path(name).name)
        for name in output.splitlines()
    )


def _diff_size(root: Path, base: str, head: str) -> tuple[int, bool]:
    output = _git(root, "diff", "--numstat", f"{base}...{head}")
    additions = 0
    total = 0
    for line in output.splitlines():
        added_text, deleted_text, _ = line.split("\t", 2)
        if added_text == "-" or deleted_text == "-":
            continue
        added, deleted = int(added_text), int(deleted_text)
        additions += added
        total += added + deleted
    return total, additions > 0


def _run_tool(command: Sequence[str]) -> None:
    subprocess.run(command, check=True, timeout=120)


def _check_push_hygiene(root: Path, head: str, issue: str | None) -> bool:
    auto_merge = os.environ.get("STAFF_PR_AUTO_MERGE", "").strip().lower() in _TRUTHY
    in_queue = os.environ.get("STAFF_PR_IN_MERGE_QUEUE", "").strip().lower() in _TRUTHY
    if not auto_merge and not in_queue:
        return True
    hygiene = _load_push_hygiene()
    if hygiene is None:
        return True
    commit = hygiene.inspect_git_commit(root, head, main_ref="origin/main")
    pr_state = hygiene.PullRequestQueueState(
        pr_number=int(issue) if issue and issue.isdigit() else None,
        auto_merge_enabled=auto_merge,
        in_merge_queue=in_queue,
    )
    decision = hygiene.evaluate_push_hygiene(commit, pr_state)
    if not decision.allowed:
        log.error("pre-push: push-hygiene: %s", decision.reason)
        return False
    return True


def run_pre_push_checks(root: Path, ref_lines: str) -> int:
    """Check the refs in Git's pre-push protocol, returning a process status."""
    root = root.resolve(strict=True)
    refs = [line.split() for line in ref_lines.splitlines() if line.strip()]
    if any(len(ref) != 4 for ref in refs):
        log.error("pre-push: refusing malformed ref input")
        return 1
    try:
        _run_tool(["git", "fetch", "origin", "main", "--quiet"])
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        log.error("pre-push: unable to refresh origin/main: %s", exc)
        return 1

    branch = _git(root, "branch", "--show-current")
    issue = _issue_number(branch)
    for _, head, _, _ in refs:
        if re.fullmatch(r"0+", head):
            continue
        try:
            if not _check_push_hygiene(root, head, issue):
                return 1
            diff_size, has_additions = _diff_size(root, "origin/main", head)
            if has_additions and diff_size >= 500:
                log.error(
                    "pre-push: diff against origin/main is %d changed lines; the limit is fewer than 500",
                    diff_size,
                )
                return 1
            if issue and not _fragment_exists(root, "origin/main", head, issue):
                log.error(
                    "pre-push: issue branch %s requires changes/%s-<slug>.md",
                    branch,
                    issue,
                )
                return 1
            python_files = _changed_python_files(root, "origin/main", head)
            if python_files:
                _run_tool(["ruff", "check", *python_files])
                _run_tool(["ruff", "format", "--check", *python_files])
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            log.error("pre-push: checks failed: %s", exc)
            return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args != ["pre-push"]:
        log.error("usage: git_hooks.py pre-push")
        return 2
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    return run_pre_push_checks(Path.cwd(), sys.stdin.read())


if __name__ == "__main__":
    raise SystemExit(main())
