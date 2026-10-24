#!/usr/bin/env python3
"""Bi-directional Windows <-> WSL provider credential mirroring for Runner Dashboard nodes.

Syncs the freshest valid credential files across:
  - Claude: ~/.config/runner-dashboard/claude/.credentials.json, ~/.claude/.credentials.json,
            and /mnt/c/Users/<winuser>/.claude/.credentials.json
  - Codex:  ~/.codex/auth.json and /mnt/c/Users/<winuser>/.codex/auth.json
  - Agy:    ~/.gemini/antigravity-cli/*.json and /mnt/c/Users/<winuser>/.gemini/antigravity-cli/*.json

Security invariant: Never prints, logs, or echoes any token or secret contents.
All WSL credential files are written atomically with mode 0600 (parent dirs 0700).
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

_SKIP_WIN_USERS = {"Public", "Default", "Default User", "All Users", "desktop.ini"}


def discover_windows_homes(mnt_users: Path = Path("/mnt/c/Users")) -> list[Path]:
    """Return candidate Windows user profile directories on WSL hosts."""
    if not mnt_users.is_dir():
        return []
    candidates: list[Path] = []
    try:
        entries = sorted(mnt_users.iterdir())
    except OSError:
        return []
    for entry in entries:
        if entry.name in _SKIP_WIN_USERS or not entry.is_dir():
            continue
        if any((entry / sub).exists() for sub in (".claude", ".codex", ".gemini")):
            candidates.append(entry)
    return candidates


def _load_json(path: Path) -> dict[str, Any] | list[Any] | None:
    try:
        if not path.is_file() or path.stat().st_size == 0:
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _is_wsl_path(path: Path) -> bool:
    return not str(path).startswith("/mnt/")


def _atomic_copy(src: Path, dst: Path) -> bool:
    """Copy src to dst atomically and set 0600 permissions on WSL paths."""
    if src.resolve() == dst.resolve():
        return False
    try:
        src_bytes = src.read_bytes()
        if dst.is_file() and dst.read_bytes() == src_bytes:
            if _is_wsl_path(dst):
                dst.chmod(0o600)
            return False
        dst.parent.mkdir(parents=True, exist_ok=True)
        if _is_wsl_path(dst.parent):
            dst.parent.chmod(0o700)
        fd, tmp_name = tempfile.mkstemp(prefix=f".{dst.name}.", dir=str(dst.parent))
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(src_bytes)
            if _is_wsl_path(dst):
                os.chmod(tmp_name, 0o600)
            os.replace(tmp_name, dst)
            st = src.stat()
            os.utime(dst, (st.st_atime, st.st_mtime))
        finally:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
        return True
    except OSError:
        return False


def _claude_score(path: Path) -> tuple[float, float] | None:
    data = _load_json(path)
    if not isinstance(data, dict):
        return None
    oauth = data.get("claudeAiOauth")
    if not isinstance(oauth, dict):
        return None
    if not (oauth.get("refreshToken") or oauth.get("accessToken")):
        return None
    expires_at = float(oauth.get("expiresAt") or 0.0)
    mtime = path.stat().st_mtime
    return (expires_at, mtime)


def _codex_score(path: Path) -> float | None:
    data = _load_json(path)
    if not isinstance(data, dict):
        return None
    tokens = data.get("tokens")
    has_tokens = isinstance(tokens, dict) and bool(tokens.get("refresh_token") or tokens.get("access_token"))
    if not (has_tokens or data.get("OPENAI_API_KEY")):
        return None
    return path.stat().st_mtime


def _agy_score(path: Path) -> float | None:
    data = _load_json(path)
    if data is None:
        return None
    if isinstance(data, (dict, list)) and len(data) == 0:
        return None
    return path.stat().st_mtime


def sync_all_credentials(
    wsl_home: Path | None = None,
    win_homes: list[Path] | None = None,
) -> dict[str, list[str]]:
    """Mirror the newest valid claude, codex, and antigravity credentials across WSL & Windows."""
    home = (wsl_home or Path.home()).expanduser()
    windows = discover_windows_homes() if win_homes is None else win_homes
    synced: dict[str, list[str]] = {"claude": [], "codex": [], "antigravity": []}

    # 1. Claude credentials
    claude_paths = [
        home / ".config" / "runner-dashboard" / "claude" / ".credentials.json",
        home / ".claude" / ".credentials.json",
        *[w / ".claude" / ".credentials.json" for w in windows],
    ]
    scored_claude = [(s, p) for p in claude_paths if (s := _claude_score(p)) is not None]
    if scored_claude:
        _, best_claude = max(scored_claude, key=lambda item: item[0])
        for target in claude_paths:
            if _atomic_copy(best_claude, target):
                synced["claude"].append(str(target))

    # 2. Codex credentials
    codex_paths = [
        home / ".codex" / "auth.json",
        *[w / ".codex" / "auth.json" for w in windows],
    ]
    scored_codex = [(s, p) for p in codex_paths if (s := _codex_score(p)) is not None]
    if scored_codex:
        _, best_codex = max(scored_codex, key=lambda item: item[0])
        for target in codex_paths:
            if _atomic_copy(best_codex, target):
                synced["codex"].append(str(target))

    # 3. Antigravity (agy) credentials
    agy_roots = [
        home / ".gemini" / "antigravity-cli",
        *[w / ".gemini" / "antigravity-cli" for w in windows],
    ]
    rel_files: set[Path] = {
        Path("accounts.json"),
        Path("active_account.json"),
        Path("oauth_creds.json"),
    }
    for root in agy_roots:
        acc_dir = root / "accounts"
        if acc_dir.is_dir():
            for child in acc_dir.glob("*.json"):
                rel_files.add(Path("accounts") / child.name)

    for rel in sorted(rel_files):
        candidates = [r / rel for r in agy_roots]
        scored_agy = [(s, p) for p in candidates if (s := _agy_score(p)) is not None]
        if scored_agy:
            _, best_agy = max(scored_agy, key=lambda item: item[0])
            for target in candidates:
                if _atomic_copy(best_agy, target):
                    synced["antigravity"].append(str(target))

    return synced


if __name__ == "__main__":
    result = sync_all_credentials()
    sys.stdout.write(json.dumps({"ok": True, "synced": result}) + "\n")
