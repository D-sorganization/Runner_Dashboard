"""API key management helpers — load or generate the dashboard API key.

Extracted from server.py (issue #2942).

Public API
----------
load_or_generate_api_key() — load from env/file, generate+persist if missing
"""

from __future__ import annotations

import logging
import os
import secrets
from pathlib import Path

log = logging.getLogger("dashboard")


def load_or_generate_api_key() -> str:
    """Return the dashboard API key, generating one if not set.

    Precondition: none (environment may or may not have DASHBOARD_API_KEY set).
    Postcondition: returns a non-empty API key string.
    """
    key_from_env = os.environ.get("DASHBOARD_API_KEY", "").strip()
    if key_from_env:
        return key_from_env
    key_file = (
        Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
        / "runner-dashboard"
        / "api_key.txt"
    )
    try:
        if key_file.exists():
            stored = key_file.read_text(encoding="utf-8").strip()
            if stored:
                return stored
    except OSError:
        pass
    new_key = secrets.token_urlsafe(32)
    try:
        key_file.parent.mkdir(parents=True, exist_ok=True)
        key_file.write_text(new_key, encoding="utf-8")
        key_file.chmod(0o600)
        log.warning("Generated new API key; saved to %s", key_file)
        log.warning("Add header 'Authorization: Bearer %s' to all API requests.", new_key)
    except OSError as exc:
        log.warning("Could not persist API key to %s: %s", key_file, exc)
    assert new_key, "generated API key must be non-empty"
    return new_key
