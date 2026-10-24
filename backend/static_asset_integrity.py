"""Read-only comparison of deployed static assets with artifact metadata."""

from __future__ import annotations

import hashlib
import re
import stat
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any

_SCHEMA = "runner-dashboard-static-assets-v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MAX_ENTRIES = 512
_MAX_FILE_BYTES = 32 * 1024 * 1024
_MAX_TOTAL_BYTES = 256 * 1024 * 1024
_CHUNK_BYTES = 1024 * 1024


def _entries(manifest: Any) -> list[tuple[str, int, str]] | None:
    if not isinstance(manifest, Mapping) or manifest.get("schema") != _SCHEMA:
        return None
    raw_entries = manifest.get("entries")
    if not isinstance(raw_entries, list) or not raw_entries or len(raw_entries) > _MAX_ENTRIES:
        return None

    entries: list[tuple[str, int, str]] = []
    seen: set[str] = set()
    total = 0
    for item in raw_entries:
        if not isinstance(item, Mapping):
            return None
        raw_path = item.get("path")
        size = item.get("size_bytes")
        digest = item.get("sha256")
        if not isinstance(raw_path, str) or "\\" in raw_path or "\x00" in raw_path:
            return None
        path = PurePosixPath(raw_path)
        if path.is_absolute() or path.as_posix() != raw_path or any(part in {"", ".", ".."} for part in path.parts):
            return None
        if raw_path in seen or type(size) is not int or size < 0 or size > _MAX_FILE_BYTES:
            return None
        if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
            return None
        seen.add(raw_path)
        total += size
        if total > _MAX_TOTAL_BYTES:
            return None
        entries.append((raw_path, size, digest))
    return entries


def _asset_target(root: Path, name: str) -> tuple[str, Path | None, Any | None]:
    current = root
    parts = PurePosixPath(name).parts
    info: Any | None = None
    for index, part in enumerate(parts):
        current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError:
            return "missing", None, None
        except OSError:
            return "unreadable", None, None
        if stat.S_ISLNK(info.st_mode):
            return "changed", None, None
        if index < len(parts) - 1 and not stat.S_ISDIR(info.st_mode):
            return "changed", None, None
    if info is None or not stat.S_ISREG(info.st_mode):
        return "changed", None, None
    return "ready", current, info


def inspect_static_asset_integrity(manifest: Any, dist_dir: Path) -> dict[str, Any]:
    """Compare expected bytes with dist; never return absolute paths or raise on bad data."""
    entries = _entries(manifest)
    if entries is None:
        reason = "legacy_manifest" if manifest is None else "invalid_manifest"
        return {"status": "unavailable", "reason": reason, "checked_files": 0, "changed": [], "missing": []}
    try:
        root = dist_dir.resolve(strict=True)
    except OSError:
        return {"status": "unavailable", "reason": "dist_unavailable", "checked_files": 0, "changed": [], "missing": []}
    if not root.is_dir():
        return {"status": "unavailable", "reason": "dist_unavailable", "checked_files": 0, "changed": [], "missing": []}

    changed: list[str] = []
    missing: list[str] = []
    checked = 0
    total_read = 0
    for name, expected_size, expected_hash in entries:
        state, candidate, info = _asset_target(root, name)
        if state == "missing":
            missing.append(name)
            continue
        if state == "unreadable":
            return {
                "status": "unavailable",
                "reason": "asset_unavailable",
                "checked_files": checked,
                "changed": [],
                "missing": [],
            }
        if state != "ready" or candidate is None or info is None or info.st_size != expected_size:
            changed.append(name)
            continue

        digest = hashlib.sha256()
        bytes_read = 0
        try:
            with candidate.open("rb") as stream:
                while bytes_read < expected_size:
                    allowance = min(expected_size - bytes_read, _MAX_TOTAL_BYTES - total_read)
                    if allowance <= 0:
                        return {
                            "status": "unavailable",
                            "reason": "byte_limit",
                            "checked_files": checked,
                            "changed": [],
                            "missing": [],
                        }
                    chunk = stream.read(min(_CHUNK_BYTES, allowance))
                    if not chunk:
                        break
                    bytes_read += len(chunk)
                    total_read += len(chunk)
                    digest.update(chunk)
            current_size = candidate.stat().st_size
        except FileNotFoundError:
            missing.append(name)
            continue
        except OSError:
            return {
                "status": "unavailable",
                "reason": "asset_unavailable",
                "checked_files": checked,
                "changed": [],
                "missing": [],
            }
        if bytes_read != expected_size or current_size != expected_size or digest.hexdigest() != expected_hash:
            changed.append(name)
        else:
            checked += 1

    return {
        "status": "drift" if changed or missing else "match",
        "checked_files": checked,
        "changed": changed,
        "missing": missing,
    }
