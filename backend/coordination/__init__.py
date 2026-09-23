"""Fleet Coordination API (epic #1192, issue #1229).

Who is working on what, messages between agents, issue claims and the
pre-work briefing — served over HTTP so every agent (Claude Code, Codex,
Gemini CLI, Grok Bot, staff runs, humans) uses one surface.

Boundaries: presence and messages stay on the Repository_Management board
issue and leases stay as issue comments plus ``claim:*`` labels. This package
only runs the RM scripts as subprocesses (``rm_scripts``); it stores nothing.
"""
