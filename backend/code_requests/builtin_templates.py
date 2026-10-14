"""Built-in prompt templates shipped with the dashboard.

Saved templates live in an operator-local JSON file, so a fresh install has
none. Built-ins give every install the same starting set; a saved template
with the same name overrides its built-in.
"""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger("dashboard.code_requests.builtin_templates")

BUILTIN_TEMPLATES_DIR = Path(__file__).resolve().parent / "builtin_prompts"


def load_builtin_templates(directory: Path = BUILTIN_TEMPLATES_DIR) -> list[dict]:
    """Return one template per ``*.md`` file in ``directory``, sorted by name.

    Each entry carries the body under both ``content`` (the backend's stored
    key) and ``prompt`` (the key the Code Requests page reads).
    """
    templates: list[dict] = []
    for path in sorted(directory.glob("*.md")):
        try:
            body = path.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError) as exc:
            log.warning("Skipping unreadable built-in template %s: %s", path.name, exc)
            continue
        if body:
            templates.append({"name": path.stem, "content": body, "prompt": body, "builtin": True})
    return templates


def merge_with_builtins(saved: list[dict], builtins: list[dict]) -> list[dict]:
    """Return saved templates plus built-ins whose name no saved template uses.

    Every entry gets a ``prompt`` alias of ``content`` so the UI can load it.
    """
    merged = [{**t, "prompt": t.get("prompt") or t.get("content", "")} for t in saved if isinstance(t, dict)]
    saved_names = {t.get("name") for t in merged}
    merged.extend(t for t in builtins if t["name"] not in saved_names)
    return merged
