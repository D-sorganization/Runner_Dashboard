"""Operator-scoped personal avatar storage and validation contract (#3500)."""

from __future__ import annotations

import hashlib
import os
import re
import struct
import tempfile
import threading
from pathlib import Path

from staff.store import _config_dir

MAX_AVATAR_BYTES = 1024 * 1024  # 1 MiB
MIN_DIMENSION, MAX_DIMENSION, MAX_PIXELS = 16, 2048, 1024 * 1024
SAFE_ID_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


class AvatarValidationError(ValueError):
    """Raised for any malformed, truncated, oversized, or unsupported image."""


def default_personal_avatars_dir() -> Path:
    default = str(_config_dir() / "personal_avatars")
    return Path(os.environ.get("STAFF_PERSONAL_AVATARS_DIR", default)).expanduser()


def validate_safe_id(val: str, kind: str = "identifier") -> None:
    if not isinstance(val, str) or not val or not SAFE_ID_RE.match(val):
        raise AvatarValidationError(f"invalid {kind} {val!r}: must match [a-zA-Z0-9_-]+")


def _inspect_webp(data: bytes) -> tuple[int, int, str, bytes]:
    if len(data) < 30 or data[0:4] != b"RIFF" or data[8:12] != b"WEBP":
        raise AvatarValidationError("invalid WebP magic header")
    if len(data) < struct.unpack("<I", data[4:8])[0] + 8:
        raise AvatarValidationError("truncated WebP container")
    chunk, sz = data[12:16], struct.unpack("<I", data[16:20])[0]
    if chunk == b"VP8 ":
        if len(data) < 20 + sz or sz < 10 or (data[20] & 1) != 0 or data[23:26] != b"\x9d\x01\x2a":
            raise AvatarValidationError("invalid VP8 chunk")
        w, h = (
            struct.unpack("<H", data[26:28])[0] & 0x3FFF,
            struct.unpack("<H", data[28:30])[0] & 0x3FFF,
        )
    elif chunk == b"VP8L":
        if len(data) < 20 + sz or sz < 5 or data[20] != 0x2F:
            raise AvatarValidationError("invalid VP8L chunk")
        v = struct.unpack("<I", data[21:25])[0]
        w, h = (v & 0x3FFF) + 1, ((v >> 14) & 0x3FFF) + 1
    elif chunk == b"VP8X":
        if len(data) < 20 + sz or sz < 10:
            raise AvatarValidationError("truncated VP8X chunk")
        w = int.from_bytes(data[24:27], "little") + 1
        h = int.from_bytes(data[27:30], "little") + 1
    else:
        raise AvatarValidationError(f"unsupported WebP chunk {chunk!r}")
    return w, h, "image/webp", data


def _inspect_png(data: bytes) -> tuple[int, int, str, bytes]:
    if (
        len(data) < 33
        or data[0:8] != b"\x89PNG\r\n\x1a\n"
        or struct.unpack(">I", data[8:12])[0] != 13
        or data[12:16] != b"IHDR"
    ):
        raise AvatarValidationError("invalid PNG signature or IHDR chunk")
    w, h = struct.unpack(">II", data[16:24])
    clean, pos, has_iend = [data[0:8]], 8, False
    while pos + 8 <= len(data):
        fourcc, tot = (
            data[pos + 4 : pos + 8],
            12 + struct.unpack(">I", data[pos : pos + 4])[0],
        )
        if pos + tot > len(data):
            raise AvatarValidationError("truncated PNG chunk")
        if fourcc in (b"IHDR", b"PLTE", b"IDAT", b"tRNS", b"IEND"):
            clean.append(data[pos : pos + tot])
        if fourcc == b"IEND":
            has_iend = True
            break
        pos += tot
    if not has_iend:
        raise AvatarValidationError("PNG missing IEND chunk")
    return w, h, "image/png", b"".join(clean)


def inspect_and_normalize_image(data: bytes) -> tuple[int, int, str, bytes]:
    if len(data) > MAX_AVATAR_BYTES or len(data) < 16:
        raise AvatarValidationError("avatar payload size out of bounds")
    if data.startswith(b"RIFF") and len(data) >= 12 and data[8:12] == b"WEBP":
        w, h, media, norm = _inspect_webp(data)
    elif data.startswith(b"\x89PNG\r\n\x1a\n"):
        w, h, media, norm = _inspect_png(data)
    else:
        raise AvatarValidationError("unsupported image format: must be valid WebP or PNG")
    if not (MIN_DIMENSION <= w <= MAX_DIMENSION and MIN_DIMENSION <= h <= MAX_DIMENSION and w * h <= MAX_PIXELS):
        raise AvatarValidationError(f"invalid image dimensions {w}x{h} or pixel count {w * h}")
    return w, h, media, norm


class PersonalAvatarStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or default_personal_avatars_dir()).resolve()
        self._lock = threading.Lock()

    def _target_path(self, operator_id: str, role_id: str) -> Path:
        validate_safe_id(operator_id, "operator_id")
        validate_safe_id(role_id, "role_id")
        target = (self.root / operator_id / role_id).resolve()
        target.relative_to(self.root)
        return target

    def save_avatar(self, operator_id: str, role_id: str, image_bytes: bytes) -> tuple[Path, str, str]:
        _w, _h, media_type, norm = inspect_and_normalize_image(image_bytes)
        target = self._target_path(operator_id, role_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        content_hash = hashlib.sha256(norm).hexdigest()[:12]
        with self._lock:
            with tempfile.NamedTemporaryFile(dir=target.parent, prefix=f"{role_id}.tmp.", delete=False) as tmp:
                tmp_path = Path(tmp.name)
                tmp.write(norm)
                tmp.flush()
                os.fsync(tmp.fileno())
            try:
                os.replace(tmp_path, target)
                target.with_suffix(".meta").write_text(media_type, encoding="utf-8")
            except Exception:
                tmp_path.unlink(missing_ok=True)
                raise
        return target, media_type, content_hash

    def get_avatar(self, operator_id: str, role_id: str) -> tuple[Path, str] | None:
        target = self._target_path(operator_id, role_id)
        if not target.is_file():
            return None
        meta = target.with_suffix(".meta")
        media = meta.read_text(encoding="utf-8").strip() if meta.is_file() else "image/webp"
        return target, media or "image/webp"

    def reset_avatar(self, operator_id: str, role_id: str) -> bool:
        target = self._target_path(operator_id, role_id)
        with self._lock:
            deleted = target.is_file()
            target.unlink(missing_ok=True)
            target.with_suffix(".meta").unlink(missing_ok=True)
        return deleted


_STORE: PersonalAvatarStore | None = None
_STORE_LOCK = threading.Lock()


def get_personal_avatar_store() -> PersonalAvatarStore:
    global _STORE
    with _STORE_LOCK:
        if _STORE is None:
            _STORE = PersonalAvatarStore()
        return _STORE
