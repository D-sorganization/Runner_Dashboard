# Vendored from Repository_Management scripts/automerge_guard.py
# (commit dac5db339cd9a62d3a8d49f7d1aebc3e20fbd9ba; pending RM#1939). Re-sync from upstream; do
# not fork. No local changes except ruff format at this repository's line
# length. collate-changes.yml arms auto-merge only through this guard.
# Vendored files follow upstream size; any line-length split happens in RM (RM#1938).
"""Refuse to arm GitHub auto-merge on a pull request a reviewer has held back.

WHY THIS EXISTS
---------------
Fleet automation arms auto-merge using the repository owner's credentials, so
its arm is indistinguishable from a human's. That makes a reviewer's
``gh pr merge <n> --disable-auto`` unenforceable: the next automation pass just
re-arms it. Measured in ``Gasification_Model`` on 2026-08-14, every event
attributed to ``dieterolson`` (``type=User``, not a bot):

===== ================= ================= ======
PR    disarmed          re-armed          gap
===== ================= ================= ======
#4692 04:15:08Z         04:15:16Z         8s
#4710 04:15:39Z         04:15:45Z         6s
#4711 04:15:50Z         04:15:56Z         6s
#4709 03:38:35Z         04:02:09Z         24m
===== ================= ================= ======

PR #4709 is why this matters: it had auto-merge armed on a diff that DELETED 13
files present on ``main``. The reviewer's only remaining option was to close it.

Every fleet code path that arms auto-merge must go through :func:`arm_auto_merge`
rather than calling ``gh pr merge --auto`` directly. One seam, one policy.

This is the *arming* side of the fix. The enforcing side is the
``Merge-Hold-Guard.yml`` workflow deployed to each repo, which revokes arms this
module failed to prevent (for example, from an agent that shells out on its
own). Neither replaces the other: this one avoids the fight, the guard wins it.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

#: Labels that mean "a human said no". Case-insensitive.
#: ``do-not-automate`` is the pre-existing fleet-wide convention
#: (shared_scripts/agent_identity.DO_NOT_AUTOMATE_LABEL); honouring it here
#: keeps one vocabulary rather than a parallel one.
HOLD_LABELS = frozenset({"do-not-merge", "blocked", "do-not-automate"})

#: Label that acknowledges an intentional deletion of tracked files.
DELETIONS_ACK_LABEL = "deletions-acknowledged"

#: Body opt-out, for repos where the reviewer cannot apply labels.
DELETIONS_ACK_BODY = "deletions-acknowledged:"

#: Injected in tests so the whole module runs hermetically — no gh, no network.
CommandRunner = Callable[[Sequence[str]], "subprocess.CompletedProcess[str]"]


def _default_runner(cmd: Sequence[str]) -> subprocess.CompletedProcess[str]:
    """Run ``cmd`` and capture output. Resolved via PATH so Windows finds gh.exe."""
    exe = shutil.which(cmd[0]) or cmd[0]
    return subprocess.run(
        [exe, *cmd[1:]],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


@dataclass(frozen=True)
class HoldVerdict:
    """Why a pull request may not have auto-merge armed."""

    held: bool
    reasons: tuple[str, ...] = ()
    #: Populated on evaluation failure. A verdict that could not be computed is
    #: treated as HELD, so a broken token or a rate limit never opens the gate.
    error: str | None = None

    def describe(self) -> str:
        if not self.held:
            return "no hold in effect"
        return "; ".join(self.reasons) or "unknown hold"


@dataclass(frozen=True)
class ArmResult:
    """Outcome of an :func:`arm_auto_merge` call."""

    armed: bool
    verdict: HoldVerdict
    detail: str = ""


@dataclass
class _PullRequest:
    number: int
    draft: bool
    labels: list[str] = field(default_factory=list)
    body: str = ""
    head_sha: str = ""


def _gh_json(runner: CommandRunner, args: Sequence[str]) -> object:
    proc = runner(["gh", *args])
    if proc.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args)} failed: {proc.stderr.strip()}")
    text = proc.stdout.strip()
    return json.loads(text) if text else None


def _gh_lines(runner: CommandRunner, args: Sequence[str]) -> list[str]:
    proc = runner(["gh", *args])
    if proc.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args)} failed: {proc.stderr.strip()}")
    return [line for line in proc.stdout.splitlines() if line.strip()]


def _head_arrival(runner: CommandRunner, repo: str, sha: str) -> str:
    """Return the GitHub-side time the head ``sha`` reached ``repo``, or ``""``.

    Pre: ``sha`` is a commit SHA of ``repo``. Post: an ISO-8601 UTC timestamp
    assigned by GitHub, or the empty string when none can be established.

    Why not the commit's ``committer.date``: the contributor writes it, so a
    future-dated commit would postdate any reviewer disarm and let automation
    re-arm at once. The earliest ``created_at`` among the check suites for the
    SHA is set by GitHub when the push lands and cannot be forged. (The PR
    timeline's ``committed`` events carry the same author-controlled date, and
    ``head_ref_force_pushed`` covers only force-pushes.) A later suite for the
    same SHA never moves the minimum.
    """
    if not sha:
        return ""
    lines = _gh_lines(
        runner,
        [
            "api",
            f"repos/{repo}/commits/{sha}/check-suites?per_page=100",
            "--paginate",
            "--jq",
            ".check_suites[].created_at",
        ],
    )
    return min(lines) if lines else ""


def evaluate_hold(repo: str, pr: int, *, runner: CommandRunner | None = None) -> HoldVerdict:
    """Decide whether ``repo#pr`` is held back from auto-merge.

    ``repo`` is ``owner/name``. Signals mirror ``Merge-Hold-Guard.yml`` exactly,
    so the arming side and the enforcing side never disagree:

    1. a ``do-not-merge`` or ``blocked`` label
    2. draft state
    3. auto-merge disabled by a non-bot account more recently than the head
       commit — a reviewer said no and nobody has pushed since
    4. the diff deletes tracked files with no acknowledgement

    Fails closed: any error computing the verdict returns ``held=True``.
    """
    run = runner or _default_runner
    reasons: list[str] = []

    try:
        raw = _gh_json(
            run,
            ["api", f"repos/{repo}/pulls/{pr}"],
        )
        if not isinstance(raw, dict):
            return HoldVerdict(True, (), error=f"unreadable PR {repo}#{pr}")

        pull = _PullRequest(
            number=pr,
            draft=bool(raw.get("draft")),
            labels=[str(item.get("name", "")) for item in raw.get("labels") or []],
            body=str(raw.get("body") or ""),
            head_sha=str((raw.get("head") or {}).get("sha") or ""),
        )
        lowered = {label.lower() for label in pull.labels}

        # --- signal 1: explicit hold labels --------------------------------
        for label in sorted(lowered & HOLD_LABELS):
            reasons.append(f"`{label}` label")

        # --- signal 2: draft ------------------------------------------------
        if pull.draft:
            reasons.append("PR is a draft (mark ready first)")

        # --- signal 3: a reviewer disarmed it since the last push -----------
        # Bot actors are excluded so the Merge-Hold-Guard workflow's own
        # revocations never read as a human decision.
        disarms = _gh_lines(
            run,
            [
                "api",
                f"repos/{repo}/issues/{pr}/timeline?per_page=100",
                "--paginate",
                "--jq",
                '.[] | select(.event == "auto_merge_disabled") '
                '| select((.actor.type // "User") != "Bot") | .created_at',
            ],
        )
        arrived = _head_arrival(run, repo, pull.head_sha) if disarms else ""
        if disarms:
            last_disarm = max(disarms)
            if not arrived:
                reasons.append(
                    f"a reviewer disabled auto-merge at {last_disarm} and the "
                    "server-side arrival time of the head commit is unknown"
                )
            elif last_disarm > arrived:
                reasons.append(
                    f"a reviewer disabled auto-merge at {last_disarm}, after the "
                    f"head commit arrived ({arrived}) — no push has superseded it"
                )

        # --- signal 4: unacknowledged deletion of tracked files -------------
        removed = _gh_lines(
            run,
            [
                "api",
                f"repos/{repo}/pulls/{pr}/files?per_page=100",
                "--paginate",
                "--jq",
                '.[] | select(.status == "removed") | .filename',
            ],
        )
        if removed and not _deletions_acknowledged(lowered, pull.body):
            sample = ", ".join(removed[:5])
            more = f" (+{len(removed) - 5} more)" if len(removed) > 5 else ""
            reasons.append(f"diff deletes {len(removed)} tracked file(s) with no acknowledgement: {sample}{more}")
    except (RuntimeError, json.JSONDecodeError, OSError) as exc:
        # Fail closed. An unknown state is not a licence to arm.
        return HoldVerdict(True, (), error=str(exc))

    return HoldVerdict(bool(reasons), tuple(reasons))


def _deletions_acknowledged(lowered_labels: set[str], body: str) -> bool:
    if DELETIONS_ACK_LABEL in lowered_labels:
        return True
    for line in body.splitlines():
        stripped = line.strip().lower()
        if stripped.startswith(DELETIONS_ACK_BODY):
            value = stripped[len(DELETIONS_ACK_BODY) :].strip()
            if value in {"yes", "true"}:
                return True
    return False


def arm_auto_merge(
    repo: str,
    pr: int,
    *,
    strategy: str = "squash",
    delete_branch: bool = False,
    runner: CommandRunner | None = None,
) -> ArmResult:
    """Arm auto-merge on ``repo#pr`` unless a reviewer has held it back.

    This is the ONLY sanctioned way for fleet automation to arm auto-merge.
    Calling ``gh pr merge --auto`` directly bypasses the reviewer's decision and
    is what this module exists to stop.
    """
    run = runner or _default_runner
    verdict = evaluate_hold(repo, pr, runner=run)
    if verdict.held:
        logger.warning("Refusing to arm auto-merge on %s#%s: %s", repo, pr, verdict.describe())
        return ArmResult(False, verdict, "held")

    cmd = ["gh", "pr", "merge", str(pr), "--repo", repo, f"--{strategy}", "--auto"]
    if delete_branch:
        cmd.append("--delete-branch")
    proc = run(cmd)
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip()
        logger.warning("Could not arm auto-merge on %s#%s: %s", repo, pr, detail)
        return ArmResult(False, verdict, detail)

    logger.info("Auto-merge armed on %s#%s (%s).", repo, pr, strategy)
    return ArmResult(True, verdict, "armed")


def main(argv: Sequence[str] | None = None) -> int:
    """CLI: ``automerge_guard.py <owner/repo> <pr> [--arm] [--strategy squash]``.

    Without ``--arm`` this only reports. Exit code 0 means "safe to arm", 1
    means held (or, with ``--arm``, that arming did not happen).
    """
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("repo", help="owner/name")
    parser.add_argument("pr", type=int)
    parser.add_argument("--arm", action="store_true", help="arm auto-merge if not held")
    parser.add_argument("--strategy", default="squash", choices=["squash", "merge", "rebase"])
    parser.add_argument("--delete-branch", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if args.arm:
        result = arm_auto_merge(
            args.repo,
            args.pr,
            strategy=args.strategy,
            delete_branch=args.delete_branch,
        )
        if result.armed:
            print(f"armed {args.repo}#{args.pr}")
            return 0
        print(f"NOT armed {args.repo}#{args.pr}: {result.verdict.describe()}")
        return 1

    verdict = evaluate_hold(args.repo, args.pr)
    print(f"{args.repo}#{args.pr}: {verdict.describe()}")
    return 1 if verdict.held else 0


if __name__ == "__main__":
    raise SystemExit(main())
