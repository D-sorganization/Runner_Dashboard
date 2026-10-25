"""Claim-aware staff admission (#2562): never start a staff run on work another actor owns.

Several actors work one backlog (RD staff runs, codex ``claim:codex``, cursor
``claim:cursor-agent``, Claude Code ``claim:claude``, shell orchestrators). Before a
staff run is admitted against an issue or PR the hub checks the target's GitHub labels
for any ``claim:*`` that RD does not own, and (issues only) the open PRs for a closing
keyword (``Closes #N``). RD's own dispatch exclusions, active runs and holds are
enforced by ``dispatch_service`` / ``scheduler`` before this runs.

RD's own labels are ``claim:staff`` and the role labels ``claim:issue-remediator`` /
``claim:pr-remediator``; a run applies its role label (else ``claim:staff``) on admission
and removes it on a terminal status. A leftover own label never blocks RD.

Config: ``STAFF_CLAIM_GUARD`` (default on) gates the whole check; ``STAFF_CLAIM_LABEL_APPLY``
(default on) gates writing/removing the label. Manual dispatch surfaces a GitHub failure as a
warning (premise checks already fail open); the scheduler fails closed.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess  # noqa: S404
import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote

from fastapi import HTTPException
from identity import Principal, format_caller, principal_has_scope

log = logging.getLogger("dashboard.staff.claim_guard")

CLAIM_PREFIX = "claim:"
STAFF_LABEL = "claim:staff"
ROLE_LABELS = frozenset({"claim:issue-remediator", "claim:pr-remediator"})
OWN_LABELS = frozenset({STAFF_LABEL}) | ROLE_LABELS
TERMINAL_STATUSES = frozenset({"succeeded", "failed", "cancelled"})
OVERRIDE_SCOPE = "staff.admin"
DEFAULT_ORG = "D-sorganization"
PULLS_TTL_SECONDS = 30.0
GH_TIMEOUT_SECONDS = 20

_CLOSING = r"(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)"
_SKIP_LABELS = frozenset(
    {
        "blocked",
        "do-not-automate",
        "judgement:contested",
        "judgement:design",
        "measurement",
        "research",
    }
)
ISSUE_ATTEMPT_CAP = max(1, int(float(os.environ.get("STAFF_ISSUE_ATTEMPT_CAP", "3"))))
ISSUE_ATTEMPT_WINDOW = timedelta(hours=max(1.0, float(os.environ.get("STAFF_ISSUE_ATTEMPT_WINDOW_HOURS", "24"))))
NO_CHANGE_COOLDOWN = timedelta(hours=max(1.0, float(os.environ.get("STAFF_NO_CHANGE_COOLDOWN_HOURS", "24"))))

AsyncGet = Callable[[str], Awaitable[Any]]
AsyncWrite = Callable[..., Awaitable[Any]]
SyncFetch = Callable[[str], Any]

# run id -> (full repo, issue, label) for labels this process applied.
_applied: dict[str, tuple[str, int, str]] = {}
_applied_lock = threading.Lock()


def reset_applied() -> None:
    """Forget applied-label bookkeeping (tests)."""
    with _applied_lock:
        _applied.clear()


def _flag(name: str, default: str = "1") -> bool:
    return os.environ.get(name, default).strip().lower() not in ("0", "false", "no", "off", "")


def enabled() -> bool:
    return _flag("STAFF_CLAIM_GUARD")


def label_apply_enabled() -> bool:
    return enabled() and _flag("STAFF_CLAIM_LABEL_APPLY")


def full_repo(repo: str) -> str:
    parts = repo.strip().split("/")
    return f"{parts[-2]}/{parts[-1]}" if len(parts) >= 2 else f"{DEFAULT_ORG}/{parts[-1]}"


def claim_label_for(role: str) -> str:
    """Role label when one exists in the taxonomy, else ``claim:staff``."""
    candidate = f"{CLAIM_PREFIX}{role}"
    return candidate if candidate in ROLE_LABELS else STAFF_LABEL


@dataclass
class ClaimVerdict:
    """Outcome of one claim check. ``status``: clear | blocked | unavailable."""

    status: str
    code: str = ""
    message: str = ""
    claim_labels: list[str] = field(default_factory=list)
    pr: int | None = None
    pr_url: str = ""

    def to_detail(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "claim_labels": self.claim_labels,
            "pr": self.pr,
            "url": self.pr_url,
        }


# ── pure evaluation ──────────────────────────────────────────────────────────


def _label_names(payload: Any) -> list[str]:
    labels = payload.get("labels") if isinstance(payload, dict) else None
    names = [(lbl.get("name") if isinstance(lbl, dict) else str(lbl)) for lbl in labels or []]
    return [n for n in names if n]


def _skip_label(names: list[str]) -> str | None:
    lowered = {name.strip().lower() for name in names if name}
    matched = sorted(label for label in lowered if label in _SKIP_LABELS)
    return matched[0] if matched else None


def closing_pr_for(pulls: Any, issue: int) -> dict[str, Any] | None:
    """First open PR whose title/body has a closing keyword for ``issue`` (``Closes #7``, not ``#70``)."""
    pattern = re.compile(rf"\b{_CLOSING}\b[:\s]+(?:[\w.-]+/[\w.-]+)?#{issue}\b", re.IGNORECASE)
    for pr in pulls if isinstance(pulls, list) else []:
        if pattern.search(f"{pr.get('title') or ''}\n{pr.get('body') or ''}"):
            return pr
    return None


def evaluate(payload: Any, pulls: Any, *, repo: str, issue: int | None, pr: int | None) -> ClaimVerdict:
    labels = _label_names(payload)
    if issue:
        if skipped := _skip_label(labels):
            target = f"{repo}#{issue}"
            return ClaimVerdict(
                "blocked",
                "skip_label",
                f"{target} carries skip label '{skipped}'; automation must not dispatch this issue.",
            )
    foreign = [n for n in _label_names(payload) if n.startswith(CLAIM_PREFIX) and n not in OWN_LABELS]
    target = f"{repo}#{issue or pr}"
    if foreign:
        return ClaimVerdict(
            "blocked",
            "claimed_elsewhere",
            f"{target} carries {', '.join(foreign)} (owned by another actor); pick other work or an operator "
            "can pass force_claim_override=true with claim_override_reason.",
            claim_labels=foreign,
        )
    hit = closing_pr_for(pulls, issue) if issue else None
    if hit is not None:
        num = hit.get("number")
        url = str(hit.get("html_url") or hit.get("url") or "")
        return ClaimVerdict(
            "blocked",
            "closing_pr_exists",
            f"Open PR #{num} already closes {target}; dispatch pr-remediator on it, or pass "
            "force_claim_override=true with claim_override_reason.",
            pr=int(num) if isinstance(num, int) else None,
            pr_url=url,
        )
    return ClaimVerdict("clear")


def _parse_iso(raw: str) -> datetime | None:
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def issue_attempt_blocker(
    store: Any,
    *,
    role: str,
    repo: str,
    issue: int,
    now: datetime | None = None,
) -> ClaimVerdict | None:
    """Block repeated dispatches to one issue after retries/no_change outcomes."""
    if store is None:
        return None
    runs = store.list_runs_for_target(role=role, repo=repo, target_ref=f"#{issue}")
    if not runs:
        return None
    now_dt = now or datetime.now(UTC)
    window_start = now_dt - ISSUE_ATTEMPT_WINDOW
    active_window: list[Any] = []
    latest_no_change: tuple[Any, datetime] | None = None
    for run in runs:
        created = _parse_iso(str(run.created_at or ""))
        if created is None or created < window_start:
            continue
        if run.status != "cancelled":
            active_window.append(run)
        if str(run.verification or "") == "no_change":
            ended = _parse_iso(str(run.ended_at or "")) or created
            if latest_no_change is None or ended > latest_no_change[1]:
                latest_no_change = (run, ended)
    if len(active_window) >= ISSUE_ATTEMPT_CAP:
        return ClaimVerdict(
            "blocked",
            "attempt_cap_reached",
            (
                f"{repo}#{issue} has {len(active_window)} attempts in the last "
                f"{int(ISSUE_ATTEMPT_WINDOW.total_seconds() // 3600)}h (cap {ISSUE_ATTEMPT_CAP})"
            ),
        )
    if latest_no_change is not None:
        reset_at = latest_no_change[1] + NO_CHANGE_COOLDOWN
        if now_dt < reset_at:
            return ClaimVerdict(
                "blocked",
                "no_change_cooldown",
                (
                    f"{repo}#{issue} is cooling down after no_change until "
                    f"{reset_at.astimezone(UTC).isoformat().replace('+00:00', 'Z')}"
                ),
            )
    return None


# ── GitHub I/O (async: pooled gh_client via gh_utils; sync: gh CLI for non-loop threads) ──


async def _gh_get(endpoint: str) -> Any:
    from gh_utils import gh_api  # noqa: PLC0415

    return await gh_api(endpoint)


async def _gh_write(endpoint: str, method: str = "POST", json_body: Any = None) -> Any:
    from gh_utils import gh_api_write  # noqa: PLC0415

    return await gh_api_write(endpoint, method, json_body)


async def _open_pulls(repo_full: str, get: AsyncGet) -> Any:
    from cache_utils import cache_get, cache_set  # noqa: PLC0415

    key = f"claim_guard:pulls:{repo_full}"
    cached = cache_get(key, PULLS_TTL_SECONDS)
    if cached is not None:
        return cached
    pulls = await get(f"/repos/{repo_full}/pulls?state=open&per_page=100")
    cache_set(key, pulls)
    return pulls


async def check_claim(
    repo: str,
    *,
    issue: int | None = None,
    pr: int | None = None,
    role: str = "",
    get: AsyncGet | None = None,
) -> ClaimVerdict:
    """Label + closing-PR check for one target. Never raises: GitHub errors give ``unavailable``."""
    number = issue or pr
    if not number:
        return ClaimVerdict("clear")
    fetch = get or _gh_get
    repo_full = full_repo(repo)
    try:
        payload = await fetch(f"/repos/{repo_full}/issues/{number}")
        pulls = await _open_pulls(repo_full, fetch) if issue else []
    except Exception as exc:  # noqa: BLE001
        log.warning("claim guard: GitHub unreadable for %s#%s: %s", repo_full, number, exc)
        return ClaimVerdict(
            "unavailable", "claim_check_unavailable", f"could not read {repo_full}#{number} claims: {exc}"
        )
    return evaluate(payload, pulls, repo=repo_full, issue=issue, pr=pr)


def _gh_cli(path: str, method: str = "GET") -> Any:
    proc = subprocess.run(  # noqa: S603
        ["gh", "api", "-X", method, path],  # noqa: S607
        capture_output=True,
        text=True,
        timeout=GH_TIMEOUT_SECONDS,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "gh api failed").strip()[:300])
    return json.loads(proc.stdout) if proc.stdout.strip() else {}


def scheduler_blocker(
    repo: str,
    issue: int | None,
    pr: int | None,
    role: str,
    *,
    fetch: SyncFetch | None = None,
    store: Any | None = None,
    now: datetime | None = None,
) -> str:
    """Skip reason ('' = go) for a scheduled run that targets an issue/PR. Fails closed on GitHub errors."""
    number = issue or pr
    if not enabled() or not number:
        return ""
    attempt_block = issue_attempt_blocker(store or _live_store(), role=role, repo=repo, issue=number, now=now)
    if attempt_block is not None:
        return f"{attempt_block.code}: {attempt_block.message}"
    get = fetch or _gh_cli
    repo_full = full_repo(repo)
    try:
        payload = get(f"/repos/{repo_full}/issues/{number}")
        pulls = get(f"/repos/{repo_full}/pulls?state=open&per_page=100") if issue else []
    except Exception as exc:  # noqa: BLE001
        log.warning("claim guard: scheduler cannot read claims for %s#%s: %s", repo_full, number, exc)
        return f"claim_check_unavailable: {exc}"
    verdict = evaluate(payload, pulls, repo=repo_full, issue=issue, pr=pr)
    return f"{verdict.code}: {verdict.message}" if verdict.status == "blocked" else ""


# ── manual dispatch ──────────────────────────────────────────────────────────


def _authorize_override(cmd: Any, caller: Principal) -> None:
    if not principal_has_scope(caller, OVERRIDE_SCOPE):
        raise HTTPException(
            status_code=403,
            detail={
                "code": "claim_override_forbidden",
                "message": f"force_claim_override needs the {OVERRIDE_SCOPE} scope (operator or admin)",
            },
        )
    if not str(cmd.claim_override_reason or "").strip():
        raise HTTPException(
            status_code=422,
            detail={"code": "claim_override_reason_required", "message": "claim_override_reason is required"},
        )


async def enforce_claim_guard(cmd: Any, caller: Principal) -> dict[str, Any]:
    """Admission check for a ``DispatchCommand``. Returns response/audit extras; 409s a foreign claim.

    Pre: ``cmd`` has role/repo/issue/pr/force_claim_override/claim_override_reason/dry_run.
    Post: ``{}`` when disabled or untargeted; else ``claim_check`` in clear | unavailable |
    overridden (plus ``claim_warning`` when unavailable). Raises 403/422 for a bad override and
    409 for a claim without one; an override of a real claim is audited fail-closed (503 if audit is down).
    """
    if not enabled() or not cmd.repo or not (cmd.issue or cmd.pr):
        return {}
    if cmd.force_claim_override:
        _authorize_override(cmd, caller)
    verdict = await check_claim(cmd.repo, issue=cmd.issue, pr=cmd.pr, role=cmd.role, get=_gh_get)
    if verdict.status == "blocked":
        if not cmd.force_claim_override:
            raise HTTPException(status_code=409, detail=verdict.to_detail())
        if not cmd.dry_run:
            _audit_override(cmd, caller, verdict)
        return {"claim_check": "overridden", "claim_overridden": verdict.to_detail()}
    if verdict.status == "unavailable":
        return {"claim_check": "unavailable", "claim_warning": verdict.message}
    return {"claim_check": "clear"}


def _audit_override(cmd: Any, caller: Principal, verdict: ClaimVerdict) -> None:
    from staff.audit import AuditError, record_audit  # noqa: PLC0415

    try:
        record_audit(
            action="dispatch.claim_override",
            target=f"{full_repo(cmd.repo)}#{cmd.issue or cmd.pr}",
            principal=format_caller(caller),
            on_behalf_of=cmd.on_behalf_of,
            surface=cmd.surface,
            outcome="override",
            detail={
                "role": cmd.role,
                "reason": cmd.claim_override_reason.strip(),
                "overrode": verdict.to_detail(),
            },
            fail_closed=True,
        )
    except AuditError as exc:
        raise HTTPException(
            status_code=503, detail={"code": "audit_unavailable", "message": str(exc), "retryable": True}
        ) from exc


# ── label lifecycle ──────────────────────────────────────────────────────────


async def apply_claim(run_id: str, repo: str, issue: int, role: str, *, write: AsyncWrite | None = None) -> str | None:
    """Label the issue for an admitted run. Best effort: returns the label, or None when skipped/failed."""
    if not label_apply_enabled():
        return None
    label, repo_full = claim_label_for(role), full_repo(repo)
    try:
        await (write or _gh_write)(f"/repos/{repo_full}/issues/{issue}/labels", "POST", {"labels": [label]})
    except Exception as exc:  # noqa: BLE001
        log.warning("claim guard: could not apply %s to %s#%d: %s", label, repo_full, issue, exc)
        return None
    with _applied_lock:
        _applied[run_id] = (repo_full, issue, label)
    return label


def _live_store() -> Any:
    """The process runner's store when one exists (never constructs a runner)."""
    from staff import runner as runner_mod  # noqa: PLC0415

    live = runner_mod._runner  # noqa: SLF001
    return live.store if live is not None else None


def _others_active(run_id: str, repo_full: str, issue: int) -> bool:
    store = _live_store()
    if store is None:
        return False
    short = repo_full.rsplit("/", 1)[-1].lower()
    return any(
        r.id != run_id
        and r.target_kind == "issue"
        and r.target_ref == f"#{issue}"
        and (r.repo or "").rsplit("/", 1)[-1].lower() == short
        for r in store.active_runs()
    )


def _recover_entry(run_id: str) -> tuple[str, int, str] | None:
    """Rebuild the applied label from the run's ``claim_label`` event (survives a hub restart)."""
    store = _live_store()
    if store is None:
        return None
    for ev in store.events_after(run_id, 0, 1000):
        match = re.fullmatch(r"(\S+)#(\d+) (claim:\S+)", str(ev.get("text") or ""))
        if ev.get("kind") == "claim_label" and match:
            return match.group(1), int(match.group(2)), match.group(3)
    return None


def release_claim(
    run_id: str,
    status: str,
    *,
    write: Callable[[str, str], Any] | None = None,
    others_active: Callable[[str, str, int], bool] | None = None,
) -> bool:
    """Remove the label this process applied once the run is terminal. Best effort; True when removed."""
    if status not in TERMINAL_STATUSES:
        return False
    with _applied_lock:
        entry = _applied.get(run_id)
    if entry is None and label_apply_enabled():
        entry = _recover_entry(run_id)
    if entry is None:
        return False
    repo_full, issue, label = entry
    try:
        if (others_active or _others_active)(run_id, repo_full, issue):
            with _applied_lock:
                _applied.pop(run_id, None)
            return False  # another live staff run still owns the label
        (write or (lambda ep, m: _gh_cli(ep, m)))(
            f"/repos/{repo_full}/issues/{issue}/labels/{quote(label, safe='')}", "DELETE"
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("claim guard: could not remove %s from %s#%d: %s", label, repo_full, issue, exc)
        return False
    with _applied_lock:
        _applied.pop(run_id, None)
    return True
