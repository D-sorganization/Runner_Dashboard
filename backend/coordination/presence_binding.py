"""Presence repository binding helpers (#4624)."""

from __future__ import annotations

from coordination.board import RMScriptError, read_sessions


def resolve_release_repo(session: str, requested_repo: str) -> str:
    """Choose the repo argument for ``agent_communicate release``.

    Pre: ``session`` and ``requested_repo`` are normalized bare repo names. Post: returns
    ``requested_repo`` when the session has no live presence elsewhere or the board cannot be
    read; otherwise returns the repo where live presence is held so release clears the binding
    before a cross-repo switch.
    """
    try:
        bound = live_presence_repo(session)
    except RMScriptError:
        return requested_repo
    if bound is None or bound.casefold() == requested_repo.casefold():
        return requested_repo
    return bound


def live_presence_repo(session: str) -> str | None:
    """Repo where ``session`` holds live board presence, if any (#4624).

    Pre: ``session`` is non-empty. Post: bare repo name or ``None``. Raises ``RMScriptError``
    when the board cannot be read.
    """
    data = read_sessions()
    if not data["available"]:
        raise RMScriptError(f"board unavailable, cannot resolve presence repo for {session}: {data.get('reason')}")
    for row in data.get("sessions") or []:
        if str(row.get("session")) == session:
            repo = str(row.get("repo") or "").strip()
            return repo or None
    return None
