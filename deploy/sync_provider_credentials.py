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

import hashlib
import json
import logging
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

log = logging.getLogger("sync_provider_credentials")

_SKIP_WIN_USERS = {"Public", "Default", "Default User", "All Users", "desktop.ini"}


def discover_windows_homes(mnt_users: Path = Path("/mnt/c/Users")) -> list[Path]:
    """Return candidate Windows user profile directories on WSL hosts."""
    if not mnt_users.is_dir():
        return []
    candidates: list[Path] = []
    try:
        entries = sorted(mnt_users.iterdir())
    except OSError as exc:
        log.warning("cannot list %s: %s", mnt_users, exc)
        return []
    for entry in entries:
        if entry.name in _SKIP_WIN_USERS:
            continue
        try:
            if not entry.is_dir():
                continue
        except OSError as exc:
            log.warning("skipping unreadable profile dir %s: %s", entry, exc)
            continue
        readable = False
        for sub in (".claude", ".codex", ".gemini"):
            child = entry / sub
            try:
                if child.exists():
                    readable = True
                    break
            except OSError as exc:
                log.warning("skipping unreadable path %s: %s", child, exc)
        if readable:
            candidates.append(entry)
    return candidates


def _load_json(path: Path) -> dict[str, Any] | list[Any] | None:
    try:
        if not path.is_file():
            return None
        if path.stat().st_size == 0:
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except PermissionError as exc:
        log.warning("skipping unreadable credential file %s: %s", path, exc)
        return None
    except OSError as exc:
        log.warning("skipping credential file %s: %s", path, exc)
        return None
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


def _is_wsl_path(path: Path) -> bool:
    return not str(path).startswith("/mnt/")


def _atomic_copy(src: Path, dst: Path) -> bool:
    """Copy src to dst atomically and set 0600 permissions on WSL paths."""
    if src.resolve() == dst.resolve():
        return False
    try:
        src_bytes = src.read_bytes()
    except PermissionError as exc:
        log.warning("skipping unreadable credential source %s: %s", src, exc)
        return False
    except OSError as exc:
        log.warning("skipping credential source %s: %s", src, exc)
        return False
    try:
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
    except PermissionError as exc:
        log.warning("cannot write credential mirror %s: %s", dst, exc)
        return False
    except OSError as exc:
        log.warning("cannot write credential mirror %s: %s", dst, exc)
        return False


def _claude_oauth(path: Path) -> dict[str, Any] | None:
    data = _load_json(path)
    if not isinstance(data, dict):
        return None
    oauth = data.get("claudeAiOauth")
    if not isinstance(oauth, dict):
        return None
    if not (oauth.get("refreshToken") or oauth.get("accessToken")):
        return None
    return oauth


def _claude_score(path: Path) -> tuple[tuple[float, float, str], Path] | None:
    try:
        mtime = path.stat().st_mtime
    except OSError as exc:
        log.warning("skipping unreadable claude credential %s: %s", path, exc)
        return None
    oauth = _claude_oauth(path)
    if oauth is None:
        return None
    refresh = str(oauth.get("refreshToken") or "")
    refresh_fp = hashlib.sha256(refresh.encode("utf-8")).hexdigest()[:16] if refresh else ""
    expires_at = float(oauth.get("expiresAt") or 0.0)
    # Prefer newest source file (refresh rotation) over stale expiresAt on older copies.
    return ((mtime, expires_at, refresh_fp), path)


def _codex_score(path: Path) -> float | None:
    data = _load_json(path)
    if not isinstance(data, dict):
        return None
    tokens = data.get("tokens")
    has_tokens = isinstance(tokens, dict) and bool(tokens.get("refresh_token") or tokens.get("access_token"))
    if not (has_tokens or data.get("OPENAI_API_KEY")):
        return None
    try:
        return path.stat().st_mtime
    except OSError as exc:
        log.warning("skipping unreadable codex credential %s: %s", path, exc)
        return None


def _agy_score(path: Path) -> float | None:
    data = _load_json(path)
    if data is None:
        return None
    if isinstance(data, (dict, list)) and len(data) == 0:
        return None
    try:
        return path.stat().st_mtime
    except OSError as exc:
        log.warning("skipping unreadable antigravity credential %s: %s", path, exc)
        return None


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
        try:
            if acc_dir.is_dir():
                for child in acc_dir.glob("*.json"):
                    rel_files.add(Path("accounts") / child.name)
        except OSError as exc:
            log.warning("skipping unreadable antigravity dir %s: %s", acc_dir, exc)

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
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr, format="%(levelname)s: %(message)s")
    result = sync_all_credentials()
    sys.stdout.write(json.dumps({"ok": True, "synced": result}) + "\n")
