"""Projects tab (issue #1199, epic #1192): charter/status parsing and per-repo overview.

``charter.py`` mirrors the Repository_Management ``shared_scripts/project_charter.py``
contract (never imported across repos); ``service.py`` fetches, caches and joins
the latest project-steward run for ``routers/projects.py``.
"""
