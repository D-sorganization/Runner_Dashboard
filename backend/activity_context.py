"""Deterministic token-capped shared running context (#2862, epic #2856 slice C1)."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Literal

from activity import GAP_KINDS, MAX_SCAN_LIMIT, ActivityEnvelope, ActivityStore
from activity_sessions import IDLE_WINDOW_SECONDS, SessionSummary, _as_utc, _derive_status, _summarize
from pydantic import BaseModel, ConfigDict, Field
from staff.standards_registry import estimate_tokens as _estimate_str_tokens

DEFAULT_BUDGET_TOKENS, MAX_BUDGET_TOKENS, MIN_BUDGET_TOKENS = 1500, 4000, 1
DEFAULT_CONTEXT_SCAN_LIMIT = 10_000
PRIORITY_TIERS: tuple[str, ...] = ("conflicts", "claims", "live_sessions", "recent_events")
SourceStatus = Literal["fresh", "stale", "empty", "unavailable"]
_ACQUIRE_KINDS = {"claim", "claimed", "claim_acquired"}
_RELEASE_KINDS = {"release", "claim_released", "expiry", "claim_expired"}
_CLAIM_KINDS = _ACQUIRE_KINDS | _RELEASE_KINDS | {"conflict"}


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SourceStaleness(_FrozenModel):
    status: SourceStatus
    stale: bool
    last_event_at: str | None = None
    age_seconds: float | None = None


class ConflictSummary(_FrozenModel):
    conflict_id: str
    kind: Literal["scope_conflict", "claim_conflict"]
    repo: str
    issue: int | None = None
    sessions: list[str]
    agents: list[str]
    branches: list[str] = Field(default_factory=list)
    paths: list[str] = Field(default_factory=list)
    goals: list[str] = Field(default_factory=list)
    detected_at: str


class ClaimSummary(_FrozenModel):
    repo: str
    issue: int | None = None
    holder: str
    session_id: str
    fencing_token: str | None = None
    claimed_at: str
    expires_at: str | None = None


class LiveSessionSummary(SessionSummary):
    principal: str
    paths: list[str] = Field(default_factory=list)
    goals: dict[str, str] = Field(default_factory=dict)


class ActivityContext(_FrozenModel):
    generated_at: str
    repo: str | None = None
    budget_tokens: int
    used_tokens: int
    truncated: bool
    truncated_tiers: list[str]
    dropped_counts: dict[str, int]
    sources: dict[str, SourceStaleness]
    conflicts: list[ConflictSummary]
    claims: list[ClaimSummary]
    live_sessions: list[LiveSessionSummary]
    recent_events: list[ActivityEnvelope]


def _iso_utc(moment: datetime) -> str:
    return _as_utc(moment).isoformat().replace("+00:00", "Z")


def _parse_iso(val: object) -> datetime | None:
    try:
        return _as_utc(datetime.fromisoformat(val)) if isinstance(val, str) and val.strip() else None
    except ValueError:
        return None


def _iso_epoch(iso_str: str) -> float:
    return _as_utc(datetime.fromisoformat(iso_str.replace("Z", "+00:00"))).timestamp()


def estimate_tokens(value: Any) -> int:
    """Deterministic local token estimator (1 token per 4 UTF-8 JSON chars ceiling)."""
    if isinstance(value, str):
        return _estimate_str_tokens(value)
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return _estimate_str_tokens(json.dumps(value, separators=(",", ":"), sort_keys=True, ensure_ascii=True))


def _conflict(cid: str, kind: Any, repo: str, issue: Any, sess: Any, ag: Any, det: str, **kw: Any) -> ConflictSummary:
    base = {"conflict_id": cid, "kind": kind, "repo": repo, "issue": issue, "sessions": sess, "agents": ag}
    return ConflictSummary(**base, detected_at=det, **kw)


def _derive_live_sessions(
    chron: list[ActivityEnvelope], *, now: datetime
) -> tuple[list[LiveSessionSummary], dict[str, str], datetime | None]:
    latest: dict[tuple[str, str], ActivityEnvelope] = {}
    meta: dict[tuple[str, str], dict[str, Any]] = {}
    agents: dict[str, str] = {}
    newest_ts: datetime | None = None
    defaults: dict[str, Any] = {"paths": [], "goals": {}, "repo": None, "issue": None, "branch": None, "pr": None}
    for ev in chron:
        if ev.source == "job_board" or ev.kind in GAP_KINDS:
            continue
        key, ev_ord = (ev.source, ev.session_id), (_as_utc(ev.ts), ev.seq, ev.event_id)
        if (cur := latest.get(key)) is None or ev_ord >= (_as_utc(cur.ts), cur.seq, cur.event_id):
            latest[key] = ev
        info = meta.setdefault(key, dict(defaults))
        for f in ("repo", "issue", "branch", "pr"):
            if getattr(ev, f) is not None:
                info[f] = getattr(ev, f)
        raw_p, raw_g = ev.payload.get("paths"), ev.payload.get("goals")
        if isinstance(raw_p, Sequence) and not isinstance(raw_p, (str, bytes)):
            if cleaned := sorted({str(p).strip().strip("/") for p in raw_p if isinstance(p, str) and p.strip()}):
                info["paths"] = cleaned
        if isinstance(raw_g, Mapping):
            if goals := {str(k): str(v) for k, v in sorted(raw_g.items()) if str(k).strip()}:
                info["goals"] = goals
        if isinstance(ev.payload.get("agent"), str) and ev.payload["agent"].strip():
            agents[ev.session_id] = ev.payload["agent"].strip()
        newest_ts = _as_utc(ev.ts) if newest_ts is None or _as_utc(ev.ts) > newest_ts else newest_ts

    active = [
        LiveSessionSummary(principal=ev.principal, **{**_summarize(ev, now=now).model_dump(), **meta[k]})
        for k, ev in latest.items()
        if _derive_status(ev, now=now) not in {"stale", "ended"}
    ]
    active.sort(key=lambda s: (-_as_utc(s.last_event_at).timestamp(), -s.last_seq, s.source, s.session_id))
    return active, agents, newest_ts


def _derive_claims(
    chron: list[ActivityEnvelope], *, now: datetime
) -> tuple[list[ClaimSummary], list[ConflictSummary], datetime | None]:
    active: dict[tuple[str, int | str], ClaimSummary] = {}
    conflicts: dict[str, ConflictSummary] = {}
    newest_ts: datetime | None = None
    for ev in chron:
        if ev.source != "job_board" and ev.kind not in _CLAIM_KINDS:
            continue
        ev_ts, repo, pl = _as_utc(ev.ts), ev.repo or "", ev.payload
        newest_ts = ev_ts if newest_ts is None or ev_ts > newest_ts else newest_ts
        target: tuple[str, int | str] = (repo.casefold(), ev.issue if ev.issue is not None else ev.session_id)
        holder = str(pl.get("agent") or pl.get("holder") or pl.get("holder_principal") or ev.principal)
        sess, ts_s = str(pl.get("session") or ev.session_id), _iso_utc(ev_ts)
        if ev.kind in _RELEASE_KINDS:
            active.pop(target, None)
        elif ev.kind == "conflict":
            cid, s_pair = f"claim:{repo}:{ev.issue or 0}:{sess}", sorted({sess, ev.session_id})
            conflicts[cid] = _conflict(cid, "claim_conflict", repo, ev.issue, s_pair, [holder], ts_s)
        elif ev.kind in _ACQUIRE_KINDS:
            exp_raw = pl.get("expires_at")
            if (exp_dt := _parse_iso(exp_raw)) is not None and exp_dt <= now:
                active.pop(target, None)
                continue
            if (prev := active.get(target)) is not None and prev.session_id != sess:
                cid = f"claim:{repo}:{ev.issue or 0}:{min(prev.session_id, sess)}:{max(prev.session_id, sess)}"
                s_pair, a_pair = sorted({prev.session_id, sess}), sorted({prev.holder, holder})
                conflicts[cid] = _conflict(cid, "claim_conflict", repo, ev.issue, s_pair, a_pair, ts_s)
            tok = str(pl["fencing_token"]) if pl.get("fencing_token") else None
            exp_s = str(exp_raw) if isinstance(exp_raw, str) else None
            c_kw: dict[str, Any] = {"fencing_token": tok, "claimed_at": ts_s, "expires_at": exp_s}
            active[target] = ClaimSummary(repo=repo, issue=ev.issue, holder=holder, session_id=sess, **c_kw)
    claims = sorted(active.values(), key=lambda c: (-_iso_epoch(c.claimed_at), c.repo, c.issue or 0, c.session_id))
    return claims, list(conflicts.values()), newest_ts


def _hit_path(a: str, b: str) -> bool:
    return a == b or a.startswith(f"{b}/") or b.startswith(f"{a}/")


def _path_overlaps(left_paths: list[str], right_paths: list[str]) -> list[str]:
    arrow = chr(0x2194)
    return sorted(
        {f"{a} {arrow} {b}" for a in left_paths for b in right_paths if _hit_path(a.casefold(), b.casefold())}
    )


def _evaluate_session_conflicts(
    sessions: list[LiveSessionSummary], agents: dict[str, str], claim_conflicts: list[ConflictSummary]
) -> list[ConflictSummary]:
    """Align conflict semantics with RM #1576/#1577 session/scope conflict evaluator."""
    by_id: dict[str, ConflictSummary] = {c.conflict_id: c for c in claim_conflicts}
    ordered = sorted(sessions, key=lambda s: (s.session_id, s.source))
    for idx, left in enumerate(ordered):
        if not left.repo:
            continue
        for right in ordered[idx + 1 :]:
            if right.session_id == left.session_id or not right.repo or left.repo.casefold() != right.repo.casefold():
                continue
            paths, rg = _path_overlaps(left.paths, right.paths), right.goals
            goals = sorted({f"{k}: {v} ↔ {rg[k]}" for k, v in left.goals.items() if k in rg and v != rg[k]})
            same_issue = left.issue is not None and left.issue == right.issue
            if not (paths or goals or same_issue):
                continue
            cid = f"scope:{left.repo}:{left.session_id}:{right.session_id}"
            ag_pair = [agents.get(left.session_id, left.principal), agents.get(right.session_id, right.principal)]
            det = _iso_utc(max(_as_utc(left.last_event_at), _as_utc(right.last_event_at)))
            br, iss = [b for b in (left.branch, right.branch) if b], (left.issue if same_issue else None)
            extra = {"branches": br, "paths": paths, "goals": goals}
            by_id[cid] = _conflict(
                cid, "scope_conflict", left.repo, iss, [left.session_id, right.session_id], ag_pair, det, **extra
            )
    return sorted(by_id.values(), key=lambda c: (-_iso_epoch(c.detected_at), c.repo, c.issue or 0, c.conflict_id))


def _staleness(newest: datetime | None, *, now: datetime) -> SourceStaleness:
    if newest is None:
        return SourceStaleness(status="empty", stale=True)
    age = round(max(0.0, (now - newest).total_seconds()), 3)
    st: SourceStatus = "stale" if age > IDLE_WINDOW_SECONDS else "fresh"
    return SourceStaleness(status=st, stale=(st == "stale"), last_event_at=_iso_utc(newest), age_seconds=age)


def build_activity_context(
    store: ActivityStore,
    *,
    repo: str | None = None,
    budget_tokens: int = DEFAULT_BUDGET_TOKENS,
    scan_limit: int = DEFAULT_CONTEXT_SCAN_LIMIT,
    now: datetime | None = None,
) -> ActivityContext:
    """Build a deterministic, token-capped shared running context from ``store``."""
    if not MIN_BUDGET_TOKENS <= budget_tokens <= MAX_BUDGET_TOKENS:
        raise ValueError(f"budget_tokens must be between {MIN_BUDGET_TOKENS} and {MAX_BUDGET_TOKENS}")
    if not 1 <= scan_limit <= MAX_SCAN_LIMIT:
        raise ValueError(f"scan_limit must be between 1 and {MAX_SCAN_LIMIT}")

    moment = _as_utc(now) if now is not None else datetime.now(UTC)
    raw = store.scan_recent(repo=repo, limit=scan_limit)
    chron = sorted(raw, key=lambda e: (_as_utc(e.ts), e.seq, e.source, e.session_id, e.event_id))
    newest_all = _as_utc(chron[-1].ts) if chron else None
    newest_board = max((_as_utc(e.ts) for e in chron if e.source == "coordination"), default=None)

    live, agents, newest_sess = _derive_live_sessions(chron, now=moment)
    claims, claim_conflicts, newest_claim = _derive_claims(chron, now=moment)
    conflicts = _evaluate_session_conflicts(live, agents, claim_conflicts)
    recent = [e for e in reversed(chron) if e.kind not in GAP_KINDS]
    s_keys = ("activity_log", "claims", "live_sessions", "board")
    s_vals = (newest_all, newest_claim, newest_sess, newest_board)
    sources = {k: _staleness(ts, now=moment) for k, ts in zip(s_keys, s_vals, strict=True)}
    cands: dict[str, list[Any]] = dict(zip(PRIORITY_TIERS, (conflicts, claims, live, recent), strict=True))
    packed: dict[str, list[Any]] = {t: [] for t in PRIORITY_TIERS}
    dropped_counts: dict[str, int] = dict.fromkeys(PRIORITY_TIERS, 0)
    truncated_tiers: list[str] = []

    def _ctx(used: int, trunc: bool, tiers: Any = None, drops: Any = None) -> ActivityContext:
        t_list = list(truncated_tiers if tiers is None else tiers)
        d_map = dict(dropped_counts if drops is None else drops)
        kw: dict[str, Any] = {"generated_at": _iso_utc(moment), "repo": repo, "budget_tokens": budget_tokens, **packed}
        return ActivityContext(
            used_tokens=used, truncated=trunc, truncated_tiers=t_list, dropped_counts=d_map, sources=sources, **kw
        )

    def _sum_items() -> int:
        return sum(estimate_tokens(x) for g in packed.values() for x in g)

    w_drops = {t: len(cands[t]) for t in PRIORITY_TIERS}
    track_env = budget_tokens >= estimate_tokens(_ctx(budget_tokens, True, list(PRIORITY_TIERS), w_drops))
    halted = False
    for tier in PRIORITY_TIERS:
        items = cands[tier]
        for idx, item in enumerate(items):
            if halted:
                dropped_counts[tier] = len(items) - idx
                break
            packed[tier].append(item)
            w_est = estimate_tokens(_ctx(budget_tokens, True, list(PRIORITY_TIERS), w_drops))
            if (w_est if track_env else _sum_items()) > budget_tokens:
                packed[tier].pop()
                dropped_counts[tier] = len(items) - idx
                halted = True
                break
        if dropped_counts[tier] > 0:
            truncated_tiers.append(tier)

    is_trunc = bool(truncated_tiers) or len(raw) >= scan_limit
    if len(raw) >= scan_limit and "recent_events" not in truncated_tiers:
        truncated_tiers.append("recent_events")
    used = min(budget_tokens, estimate_tokens(_ctx(budget_tokens, is_trunc))) if track_env else _sum_items()
    return _ctx(used, is_trunc)
