"""Pure parsers from GitHub REST JSON into delivery-metrics models."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from delivery_metrics.models import CheckRunSnapshot, PrSnapshot


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def parse_pr_snapshot(repo_full: str, payload: dict[str, Any]) -> PrSnapshot:
    """Map a ``GET /repos/.../pulls/{n}`` body to :class:`PrSnapshot`.

    Pre: ``payload`` is a GitHub pull object; ``repo_full`` is ``owner/name``.
    Post: raises ``KeyError``/``TypeError`` when required fields are absent.
    """
    head = payload.get("head") or {}
    base = payload.get("base") or {}
    user = payload.get("user") or {}
    created = _parse_ts(payload.get("created_at"))
    updated = _parse_ts(payload.get("updated_at"))
    if created is None or updated is None:
        raise ValueError("pull payload missing created_at or updated_at")
    return PrSnapshot(
        repo_full=repo_full,
        number=int(payload["number"]),
        title=str(payload.get("title") or ""),
        state=str(payload.get("state") or "open"),
        merged=bool(payload.get("merged")),
        draft=bool(payload.get("draft")),
        head_sha=str(head.get("sha") or ""),
        base_ref=str(base.get("ref") or "main"),
        author_login=str(user.get("login") or ""),
        created_at=created,
        updated_at=updated,
        closed_at=_parse_ts(payload.get("closed_at")),
        merged_at=_parse_ts(payload.get("merged_at")),
        additions=int(payload.get("additions") or 0),
        deletions=int(payload.get("deletions") or 0),
        changed_files=int(payload.get("changed_files") or 0),
        html_url=str(payload.get("html_url") or ""),
    )


def parse_check_runs(repo_full: str, head_sha: str, payload: dict[str, Any]) -> tuple[CheckRunSnapshot, ...]:
    """Map a check-runs list payload to snapshots."""
    runs = payload.get("check_runs")
    if not isinstance(runs, list):
        return ()
    out: list[CheckRunSnapshot] = []
    for raw in runs:
        if not isinstance(raw, dict):
            continue
        app = raw.get("app") if isinstance(raw.get("app"), dict) else {}
        out.append(
            CheckRunSnapshot(
                repo_full=repo_full,
                head_sha=head_sha,
                check_run_id=int(raw["id"]),
                name=str(raw.get("name") or ""),
                status=str(raw.get("status") or ""),
                conclusion=raw.get("conclusion") if raw.get("conclusion") is not None else None,
                started_at=_parse_ts(raw.get("started_at")),
                completed_at=_parse_ts(raw.get("completed_at")),
                details_url=str(raw.get("details_url") or raw.get("html_url") or ""),
                app_slug=str(app.get("slug") or ""),
            )
        )
    return tuple(out)


def parse_pull_list_item(repo_full: str, payload: dict[str, Any]) -> PrSnapshot:
    """Parse an item from ``GET /repos/.../pulls`` (list endpoint)."""
    return parse_pr_snapshot(repo_full, payload)
