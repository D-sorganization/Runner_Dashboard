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


def _atomic_copy(src: Path, dst: Path, *, audit_expires_at_positive: bool | None = None) -> bool:
    """Copy src to dst atomically and set 0600 permissions on WSL paths.

    ``audit_expires_at_positive`` is set only for Claude credential writes, to log
    which source wrote a mirror and whether its OAuth token had a live (non-zero)
    expiry, without ever logging token contents.
    """
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
        if audit_expires_at_positive is not None:
            log.info(
                "credential mirror write: provider=claude caller=%s target=%s expires_at_positive=%s",
                src,
                dst,
                audit_expires_at_positive,
            )
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


def _claude_score(path: Path) -> tuple[tuple[bool, float, float, str], Path] | None:
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
    try:
        expires_at = float(oauth.get("expiresAt") or 0.0)
    except (TypeError, ValueError):
        expires_at = 0.0
    # A zeroed/missing expiresAt must never outrank a real expiry (#3971); among copies
    # with a real expiry, prefer the newest file (refresh rotation) over stale expiresAt.
    has_expiry = expires_at > 0
    return ((has_expiry, mtime, expires_at, refresh_fp), path)


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


def _stale_mirror_rows(candidates: list[Path], best: Path, *, valid: set[Path]) -> list[dict[str, Any]]:
    """Flag mirror paths that lag the chosen source (mtime or bytes), without logging secrets."""
    stale: list[dict[str, Any]] = []
    if best not in valid:
        return stale
    try:
        best_mtime = best.stat().st_mtime
        best_bytes = best.read_bytes()
    except OSError as exc:
        log.warning("skipping mirror audit for unreadable source %s: %s", best, exc)
        return stale
    for path in candidates:
        if path.resolve() == best.resolve():
            continue
        if path not in valid:
            continue
        try:
            copy_mtime = path.stat().st_mtime
            copy_bytes = path.read_bytes()
        except OSError as exc:
            log.warning("skipping mirror audit for unreadable copy %s: %s", path, exc)
            stale.append({"path": str(path), "reason": "unreadable"})
            continue
        if copy_bytes == best_bytes:
            # Byte-identical content is in sync regardless of mtime: DrvFs (/mnt/c) mirrors
            # store whole-second mtimes, so an identical copy can read up to 1s older (#4176).
            continue
        reason = "content_drift"
        if copy_mtime < best_mtime:
            reason = "older_than_source"
        stale.append(
            {
                "path": str(path),
                "reason": reason,
                "source_path": str(best),
                "copy_mtime": copy_mtime,
                "source_mtime": best_mtime,
            }
        )
    return stale


def audit_credential_mirrors(
    wsl_home: Path | None = None,
    win_homes: list[Path] | None = None,
) -> dict[str, Any]:
    """Report provider credential copies that are older than or diverged from the sync source."""
    home = (wsl_home or Path.home()).expanduser()
    windows = discover_windows_homes() if win_homes is None else win_homes
    providers: dict[str, Any] = {}

    claude_paths = [
        home / ".config" / "runner-dashboard" / "claude" / ".credentials.json",
        home / ".claude" / ".credentials.json",
        *[w / ".claude" / ".credentials.json" for w in windows],
    ]
    scored_claude = [(s, p) for p in claude_paths if (s := _claude_score(p)) is not None]
    if scored_claude:
        _, best_claude = max(scored_claude, key=lambda item: item[0])
        valid_claude = {p for _, p in scored_claude}
        stale = _stale_mirror_rows(claude_paths, best_claude, valid=valid_claude)
        providers["claude"] = {"source_path": str(best_claude), "stale_copies": stale, "ok": not stale}

    codex_paths = [
        home / ".codex" / "auth.json",
        *[w / ".codex" / "auth.json" for w in windows],
    ]
    scored_codex = [(s, p) for p in codex_paths if (s := _codex_score(p)) is not None]
    if scored_codex:
        _, best_codex = max(scored_codex, key=lambda item: item[0])
        valid_codex = {p for _, p in scored_codex}
        stale = _stale_mirror_rows(codex_paths, best_codex, valid=valid_codex)
        providers["codex"] = {"source_path": str(best_codex), "stale_copies": stale, "ok": not stale}

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

    agy_stale: list[dict[str, Any]] = []
    agy_sources: list[str] = []
    for rel in sorted(rel_files):
        candidates = [r / rel for r in agy_roots]
        scored_agy = [(s, p) for p in candidates if (s := _agy_score(p)) is not None]
        if not scored_agy:
            continue
        _, best_agy = max(scored_agy, key=lambda item: item[0])
        valid_agy = {p for _, p in scored_agy}
        agy_sources.append(str(best_agy))
        agy_stale.extend(_stale_mirror_rows(candidates, best_agy, valid=valid_agy))
    if agy_sources or agy_stale:
        providers["antigravity"] = {
            "source_paths": agy_sources,
            "stale_copies": agy_stale,
            "ok": not agy_stale,
        }

    any_stale = any(not row.get("ok", True) for row in providers.values())
    return {"ok": not any_stale, "providers": providers}


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
        best_pair = max(scored_claude, key=lambda item: item[0])
        (score_tuple, _best_claude_dup), best_claude = best_pair
        expires_at_positive = score_tuple[1] > 0
        for target in claude_paths:
            if _atomic_copy(best_claude, target, audit_expires_at_positive=expires_at_positive):
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


def _cli_main() -> int:
    """CLI entrypoint: always emit one JSON line, even on failure (RD #3975)."""
    try:
        result = sync_all_credentials()
    except Exception as exc:  # noqa: BLE001 - CLI boundary; summarized for self-heal, never the raw traceback
        summary = f"{type(exc).__name__}: {exc}"[:200]
        log.error("credential sync failed: %s", summary)
        sys.stdout.write(json.dumps({"ok": False, "error": summary}) + "\n")
        return 1
    sys.stdout.write(json.dumps({"ok": True, "synced": result}) + "\n")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr, format="%(levelname)s: %(message)s")
    sys.exit(_cli_main())
