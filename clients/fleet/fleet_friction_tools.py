"""Friction ledger and owner-question tools for the fleet client (kept apart from fleet_tools.py).

``FrictionClientMixin`` adds the client methods; ``FRICTION_COMMANDS`` are the CLI/MCP rows that
``fleet_tools.COMMANDS`` appends. API: ``/api/v1/fleet/friction`` and ``/api/v1/fleet/owner-questions``.
"""

from __future__ import annotations

from typing import Any

from fleet_validators import _check, _opt_text, _text

FRICTION_STATUSES = ("open", "in_progress", "waiting", "closed")


class FrictionClientMixin:
    """Methods mixed into ``FleetClient``; ``request`` is provided by the client."""

    def request(self, method: str, path: str, **kwargs: Any) -> Any:  # pragma: no cover - supplied by FleetClient
        raise NotImplementedError

    def friction_list(self, status: str | None = None, repo: str | None = None, limit: int = 100) -> Any:
        """List friction ledger rows, optionally filtered by status or repo."""
        _check(status is None or status in FRICTION_STATUSES, f"status must be one of {FRICTION_STATUSES}")
        _check(isinstance(limit, int) and not isinstance(limit, bool) and 1 <= limit <= 1000, "limit must be 1..1000")
        params = {"status": status, "repo": _opt_text(repo, "repo", 200), "limit": limit}
        return self.request("GET", "/api/v1/fleet/friction", params=params)

    def friction_report(
        self,
        friction_id: str | None = None,
        title: str | None = None,
        seen: str | None = None,
        repo: str | None = None,
    ) -> Any:
        """Report friction: with ``friction_id`` record a recurrence, otherwise create a new row."""
        if friction_id is not None:
            fid = _text(friction_id, "friction_id", 20, form="line")
            return self.request("POST", f"/api/v1/fleet/friction/{fid}/recurrence")
        body = {
            "title": _text(title if title is not None else "", "title", 300, form="line"),
            "seen": _text(seen if seen is not None else "", "seen", 8000),
            "repo": _opt_text(repo, "repo", 200),
        }
        return self.request("POST", "/api/v1/fleet/friction", body={k: v for k, v in body.items() if v is not None})

    def owner_question_ask(
        self,
        question: str,
        repo: str | None = None,
        issue: int | None = None,
        options: list[str] | None = None,
        recommended: str | None = None,
    ) -> Any:
        """Queue a question for the owner."""
        _check(issue is None or (isinstance(issue, int) and not isinstance(issue, bool) and issue >= 1), "bad issue")
        opts = [_text(o, "option", 300, form="line") for o in (options or [])]
        body = {
            "question": _text(question, "question", 4000),
            "repo": _opt_text(repo, "repo", 200),
            "issue": issue,
            "options": opts,
            "recommended": _opt_text(recommended, "recommended", 500),
        }
        return self.request(
            "POST", "/api/v1/fleet/owner-questions", body={k: v for k, v in body.items() if v is not None}
        )

    def owner_questions_list(self, status: str | None = None, limit: int = 100) -> Any:
        """List owner questions, optionally by status (open, answered, withdrawn)."""
        _check(status in (None, "open", "answered", "withdrawn"), "status must be open, answered or withdrawn")
        _check(isinstance(limit, int) and not isinstance(limit, bool) and 1 <= limit <= 1000, "limit must be 1..1000")
        return self.request("GET", "/api/v1/fleet/owner-questions", params={"status": status, "limit": limit})


def friction_commands(command_cls: Any) -> tuple[Any, ...]:
    """Build the CLI/MCP rows with ``fleet_tools.Command`` (passed in to avoid a circular import)."""
    repo = {"type": "string", "description": "Repository name, e.g. Runner_Dashboard."}
    limit = {"type": "integer", "minimum": 1, "maximum": 1000}
    return (
        command_cls(
            "friction-list",
            "friction_list",
            "List friction ledger rows (a row closes only with a merged fix and a detector).",
            {"status": {"type": "string", "enum": list(FRICTION_STATUSES)}, "repo": repo, "limit": limit},
            tool="fleet_friction_list",
        ),
        command_cls(
            "friction-report",
            "friction_report",
            "Report fleet friction: pass friction_id to record a recurrence, else title and seen to create a row.",
            {
                "friction_id": {"type": "string", "description": "Existing row id, e.g. F12 (records a recurrence)."},
                "title": {"type": "string", "maxLength": 300},
                "seen": {"type": "string", "description": "What was observed."},
                "repo": repo,
            },
            tool="fleet_friction_report",
        ),
        command_cls(
            "owner-question-ask",
            "owner_question_ask",
            "Queue a question for the owner with optional options and a recommendation.",
            {
                "question": {"type": "string"},
                "repo": repo,
                "issue": {"type": "integer", "minimum": 1},
                "options": {"type": "array", "items": {"type": "string"}, "maxItems": 20},
                "recommended": {"type": "string", "maxLength": 500},
            },
            required=("question",),
            tool="fleet_owner_question_ask",
        ),
        command_cls(
            "owner-questions-list",
            "owner_questions_list",
            "List owner questions (status open, answered or withdrawn).",
            {"status": {"type": "string", "enum": ["open", "answered", "withdrawn"]}, "limit": limit},
            tool="fleet_owner_questions_list",
        ),
    )
