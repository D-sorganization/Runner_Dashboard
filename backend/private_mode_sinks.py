"""Private Mode PM-6 sink adapters routing shared outputs through ``emit_shared()`` (issue #3256, epic #3249).

Adapters own surface-specific formatting only and delegate classification, redaction,
and ``SENSITIVE`` metadata projection to :func:`private_mode_emit.emit_shared` (DRY / LoD).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from activity import ActivityEnvelope
from private_mode import DISABLED, ClassificationError
from private_mode_emit import SharedProjection, emit_shared

PM6_SINKS: frozenset[str] = frozenset(
    {"board", "turnover", "inventory", "panels", "run_history", "hub_logs", "activity", "github_check", "alerts"}
)
_CHECK_RUN_STATUSES: frozenset[str] = frozenset({"queued", "in_progress", "completed"})
_CHECK_RUN_CONCLUSIONS: frozenset[str] = frozenset(
    {"action_required", "cancelled", "failure", "neutral", "skipped", "stale", "success", "timed_out"}
)
_CONCLUSION_TO_OUTCOME: Mapping[str, str] = dict(
    success="succeeded",
    failure="failed",
    cancelled="cancelled",
    skipped="ok",
    neutral="ok",
    timed_out="expired",
    action_required="pending",
    stale="expired",
)
_ALLOWED_OPTS: frozenset[str] = frozenset({"ts", "level", "config", "include_content_mac", "mac_key", "sink"})


@dataclass(frozen=True)
class SinkEmission:
    """Result of formatting and emitting a payload through a PM-6 sink adapter."""

    surface: str
    projection: SharedProjection
    formatted: dict[str, Any]


def _req_str(value: object, field_name: str, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ClassificationError(f"{where}: {field_name} must be a non-empty str")
    return value.strip()


def _str_fields(where: str, **fields: object) -> dict[str, Any]:
    return {k: _req_str(v, k, where) for k, v in fields.items()}


def _req_nonneg_int(value: object, field_name: str, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ClassificationError(f"{where}: {field_name} must be a non-negative int")
    return value


def _deliver(
    surface: str,
    raw_payload: dict[str, Any],
    formatter: Callable[[SharedProjection], dict[str, Any]],
    where: str,
    opts: Mapping[str, Any],
) -> SinkEmission:
    unknown = set(opts) - _ALLOWED_OPTS
    if unknown:
        raise ClassificationError(f"{where}: unsupported option(s): {', '.join(sorted(unknown))}")
    if opts.get("ts") is not None:
        raw_payload["ts"] = _req_str(opts["ts"], "ts", where)
    sink: Callable[[dict[str, Any]], Any] | None = opts.get("sink")
    if sink is not None and not callable(sink):
        raise ClassificationError(f"{where}: sink must be callable or None")
    projection = emit_shared(
        surface,
        raw_payload,
        level=opts.get("level"),
        config=opts.get("config", DISABLED),
        include_content_mac=bool(opts.get("include_content_mac", False)),
        mac_key=opts.get("mac_key"),
    )
    formatted = formatter(projection)
    if not isinstance(formatted, dict):
        raise ClassificationError(f"{surface}: postcondition failed — formatted output must be a dict")
    if sink is not None:
        sink(formatted)
    return SinkEmission(surface=surface, projection=projection, formatted=formatted)


def _fmt_generic(proj: SharedProjection) -> dict[str, Any]:
    if proj.withheld:
        return proj.to_dict()
    return dict(proj.content) if isinstance(proj.content, Mapping) else {"content": proj.content}


def emit_board_entry(*, entry_id: str, repo: str, author: str, status: str, body: str, **opts: Any) -> SinkEmission:
    """Format and emit a coordination-board entry via ``emit_shared('board', ...)``."""
    w = "emit_board_entry"
    p = _str_fields(w, entry_id=entry_id, repo=repo, author=author, status=status, body=body)
    p["id"] = p.pop("entry_id")
    return _deliver("board", p, _fmt_generic, w, opts)


def emit_turnover_entry(
    *, entry_id: str, scope: str, author: str, status: str, summary: str, blockers: Sequence[str] = (), **opts: Any
) -> SinkEmission:
    """Format and emit a shift-turnover entry via ``emit_shared('turnover', ...)``."""
    w = "emit_turnover_entry"
    if isinstance(blockers, (str, bytes)) or not isinstance(blockers, Sequence):
        raise ClassificationError(f"{w}: blockers must be a sequence of str")
    b_list = [_req_str(b, "blocker", w) for b in blockers]
    p = _str_fields(w, entry_id=entry_id, scope=scope, author=author, status=status, summary=summary)
    p.update(id=p.pop("entry_id"), blockers=b_list, count=len(b_list))
    return _deliver("turnover", p, _fmt_generic, w, opts)


def emit_inventory_item(
    *, item_id: str, repo: str, status: str, title: str, next_action: str = "", **opts: Any
) -> SinkEmission:
    """Format and emit a task-inventory item via ``emit_shared('inventory', ...)``."""
    w = "emit_inventory_item"
    if not isinstance(next_action, str):
        raise ClassificationError(f"{w}: next_action must be a str")
    p = _str_fields(w, item_id=item_id, repo=repo, status=status, title=title)
    p.update(id=p["item_id"], next_action=next_action)
    return _deliver("inventory", p, _fmt_generic, w, opts)


def emit_panel_entry(
    *, thread_id: str, seat: str, status: str, outcome: str, body_md: str, **opts: Any
) -> SinkEmission:
    """Format and emit a panel discussion entry via ``emit_shared('panels', ...)``."""
    w = "emit_panel_entry"
    p = _str_fields(w, thread_id=thread_id, seat=seat, status=status, outcome=outcome, body_md=body_md)
    p.update(id=p["thread_id"], role_id=p.pop("seat"))
    return _deliver("panels", p, _fmt_generic, w, opts)


def emit_run_history_entry(
    *, run_id: str, role_id: str, repo: str, status: str, outcome: str, prompt: str, **opts: Any
) -> SinkEmission:
    """Format and emit a staff run-history record via ``emit_shared('run_history', ...)``."""
    w = "emit_run_history_entry"
    ev_txt, dur_ms = opts.pop("event_text", ""), opts.pop("duration_ms", 0)
    if not isinstance(ev_txt, str):
        raise ClassificationError(f"{w}: event_text must be a str")
    p = _str_fields(w, run_id=run_id, role_id=role_id, repo=repo, status=status, outcome=outcome, prompt=prompt)
    p.update(id=p["run_id"], event_text=ev_txt, duration_ms=_req_nonneg_int(dur_ms, "duration_ms", w))
    return _deliver("run_history", p, _fmt_generic, w, opts)


def emit_hub_log_record(
    *, record_id: str, logger_name: str, severity: str, status: str, message: str, **opts: Any
) -> SinkEmission:
    """Format and emit a structured hub log record via ``emit_shared('hub_logs', ...)``."""
    w = "emit_hub_log_record"
    fields = opts.pop("fields", None)
    if fields is not None and not isinstance(fields, Mapping):
        raise ClassificationError(f"{w}: fields must be a Mapping or None")
    p = _str_fields(w, record_id=record_id, logger_name=logger_name, severity=severity, status=status, message=message)
    p.update(id=p.pop("record_id"), logger=p.pop("logger_name"), kind=p.pop("severity"), fields=dict(fields or {}))
    if fields and isinstance(fields.get("repo"), str) and fields["repo"].strip():
        p["repo"] = fields["repo"].strip()
    return _deliver("hub_logs", p, _fmt_generic, w, opts)


def emit_activity_envelope(envelope: ActivityEnvelope | Mapping[str, Any], **opts: Any) -> SinkEmission:
    """Format and emit a #2858 :class:`ActivityEnvelope` via ``emit_shared('activity', ...)``."""
    w = "emit_activity_envelope"
    if isinstance(envelope, ActivityEnvelope):
        val = envelope
    elif isinstance(envelope, Mapping):
        try:
            val = ActivityEnvelope.model_validate(dict(envelope))
        except Exception as exc:
            raise ClassificationError(f"{w}: invalid ActivityEnvelope mapping") from exc
    else:
        raise ClassificationError(f"{w}: envelope must be an ActivityEnvelope or Mapping")

    ej = val.model_dump(mode="json")
    raw_p = dict(
        id=val.event_id,
        session_id=val.session_id,
        seq=val.seq,
        kind=val.kind,
        status=str(val.payload.get("status", "ok")),
        outcome=str(val.payload.get("outcome", "ok")),
        ts=ej["ts"],
        envelope=ej,
    )
    if val.repo is not None:
        raw_p["repo"] = val.repo

    def _fmt_act(proj: SharedProjection) -> dict[str, Any]:
        if not proj.withheld:
            return ActivityEnvelope.model_validate(proj.content["envelope"]).model_dump(mode="json")
        op_repo = proj.metadata.get("repo")
        pl = dict(withheld=True, level=proj.level.name, opaque_id=proj.opaque_id, metadata=dict(proj.metadata))
        san = dict(
            ej,
            event_id=proj.opaque_id,
            session_id=str(proj.metadata.get("session_id", proj.opaque_id)),
            repo=str(op_repo) if isinstance(op_repo, str) else None,
            issue=None,
            branch=None,
            pr=None,
            payload=pl,
        )
        return ActivityEnvelope.model_validate(san).model_dump(mode="json")

    return _deliver("activity", raw_p, _fmt_act, w, opts)


def emit_github_check_run(
    *, run_id: str, repo: str, name: str, status: str, conclusion: str | None, title: str, summary: str, **opts: Any
) -> SinkEmission:
    """Format and emit a GitHub Check Run payload via ``emit_shared('github_check', ...)``."""
    w = "emit_github_check_run"
    text = opts.pop("text", None)
    p = _str_fields(w, run_id=run_id, repo=repo, name=name, status=status, title=title, summary=summary)
    chk_name, chk_status = p["name"], p["status"]
    if chk_status not in _CHECK_RUN_STATUSES:
        raise ClassificationError(f"{w}: status must be one of {sorted(_CHECK_RUN_STATUSES)}")
    if conclusion is not None and (not isinstance(conclusion, str) or conclusion not in _CHECK_RUN_CONCLUSIONS):
        raise ClassificationError(f"{w}: conclusion must be one of {sorted(_CHECK_RUN_CONCLUSIONS)} or None")
    if text is not None and not isinstance(text, str):
        raise ClassificationError(f"{w}: text must be a str or None")
    p.update(id=p["run_id"], outcome=_CONCLUSION_TO_OUTCOME.get(conclusion or "", "pending"), conclusion=conclusion)
    if text is not None:
        p["text"] = text

    def _fmt_chk(proj: SharedProjection) -> dict[str, Any]:
        if proj.withheld:
            st, oc = proj.metadata.get("status", "withheld"), proj.metadata.get("outcome", "withheld")
            ms = f"level={proj.level.name} withheld=true opaque_id={proj.opaque_id} status={st} outcome={oc}"
            out = {"title": f"Private Mode check ({chk_status})", "summary": ms}
            return dict(name=chk_name, status=chk_status, conclusion=conclusion, external_id=proj.opaque_id, output=out)
        c = proj.content
        out = {
            "title": c["title"],
            "summary": c["summary"],
            **({"text": c["text"]} if c.get("text") is not None else {}),
        }
        return dict(name=c["name"], status=c["status"], conclusion=c["conclusion"], external_id=c["run_id"], output=out)

    return _deliver("github_check", p, _fmt_chk, w, opts)


def emit_alert_event(
    *, alert_id: str, repo: str, target_id: str, kind: str, status: str, outcome: str, message: str, **opts: Any
) -> SinkEmission:
    """Format and emit an operator alert payload via ``emit_shared('alerts', ...)``."""
    w = "emit_alert_event"
    count, degraded = opts.pop("count", 1), opts.pop("degraded", False)
    if not isinstance(degraded, bool):
        raise ClassificationError(f"{w}: degraded must be a bool")
    p = _str_fields(
        w, alert_id=alert_id, repo=repo, target_id=target_id, kind=kind, status=status, outcome=outcome, message=message
    )
    aid = p.pop("alert_id")
    p.update(id=aid, item_id=aid, count=_req_nonneg_int(count, "count", w), degraded=degraded)
    return _deliver("alerts", p, _fmt_generic, w, opts)


def emit_sink(sink_name: str, payload: Mapping[str, Any], **opts: Any) -> SinkEmission:
    """Dispatch a mapping payload to any PM-6 sink surface via ``emit_shared()``."""
    if not isinstance(sink_name, str) or sink_name not in PM6_SINKS:
        raise ClassificationError(f"emit_sink: sink_name must be one of {sorted(PM6_SINKS)}")
    if not isinstance(payload, Mapping):
        raise ClassificationError("emit_sink: payload must be a Mapping")
    return _deliver(sink_name, dict(payload), _fmt_generic, "emit_sink", opts)
