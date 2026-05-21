"""Help-chat FAQ dictionary and lookup helpers.

Extracted from server.py (issue #2942).

Public API
----------
DASHBOARD_FAQ      — dict mapping keyword → answer string
faq_lookup()       — search FAQ by question text; returns match or None
tab_fallback_answer() — fallback answer for a named tab
DEFAULT_FALLBACK_ANSWER — generic fallback when no match is found
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# FAQ dictionary
# ---------------------------------------------------------------------------

DASHBOARD_FAQ: dict[str, str] = {
    "fleet": (
        "The Fleet tab shows all runners in your fleet. "
        "Use it to start/stop runners and see hardware metrics."
    ),
    "remediation": (
        "The Remediation tab lets you dispatch AI agents (Jules, Codex, Claude) to fix failing CI."
        " Move to top: Manual Dispatch is the primary control."
    ),
    "workflows": (
        "The Workflows tab lists all GitHub Actions workflows across repos."
        " Click a workflow to see run history and dispatch it manually."
    ),
    "credentials": (
        "The Credentials tab shows provider connection state."
        " No secrets are shown - only whether tools are installed and authenticated."
    ),
    "assessments": (
        "The Assessments tab lets you trigger code quality assessments for any repo"
        " and view score history."
    ),
    "feature-requests": (
        "The Feature Requests tab dispatches AI agents to implement new features"
        " with standards injection (TDD, DbC, DRY, LoD)."
    ),
    "maxwell": (
        "The Maxwell tab shows Maxwell-Daemon status and lets you start/stop the service"
        " with confirmation."
    ),
    "queue": (
        "The Queue tab shows live queued and in-progress workflows"
        " with auto-refresh every 15 seconds."
    ),
    "history": (
        "The History tab shows recent workflow runs across all repos, filterable by status."
    ),
    "machines": "The Machines tab shows hardware telemetry for each fleet node.",
    "stats": "The Stats tab shows P50/P95 duration analytics and success rates across workflows.",
    "runner-plan": (
        "The Runner Plan tab manages day/night runner capacity scheduling."
    ),
    "dispatch": (
        "To dispatch a remediation agent: go to Remediation tab, select a failed run,"
        " choose a provider, preview the plan, then dispatch."
    ),
    "provider": (
        "Providers are AI agents: Jules API (cloud, Google), Codex CLI (OpenAI),"
        " Claude Code CLI (Anthropic), Ollama (local)."
    ),
    "loop guard": (
        "Loop guard prevents infinite retry loops. When the same failure repeats more than"
        " max_same_failure_attempts times, dispatch is blocked."
    ),
}

DEFAULT_FALLBACK_ANSWER = (
    "Try the Remediation tab to dispatch agents for failing CI,"
    " or the Workflows tab to manually trigger workflows."
)


# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------


def faq_lookup(question: str, current_tab: str) -> str | None:
    """Search FAQ by matching question text against FAQ keys.

    Precondition: question and current_tab are strings.
    Postcondition: returns a matching answer string, or None if no match.
    """
    assert isinstance(question, str), "question must be a str"
    assert isinstance(current_tab, str), "current_tab must be a str"

    q_lower = question.lower()
    for key, answer in DASHBOARD_FAQ.items():
        if key in q_lower:
            return answer
    return None


def tab_fallback_answer(current_tab: str) -> str:
    """Return a tab-specific or generic fallback answer.

    Precondition: current_tab is a string.
    Postcondition: always returns a non-empty string.
    """
    assert isinstance(current_tab, str), "current_tab must be a str"

    tab_help = DASHBOARD_FAQ.get(current_tab, "")
    if tab_help:
        return f"For the {current_tab} tab: {tab_help}"
    return DEFAULT_FALLBACK_ANSWER
