"""Avatar upload validation via magic bytes and header dimension parsing (#2680)."""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Literal

from staff.role_avatars import MAX_AVATAR_ASPECT, MAX_AVATAR_BYTES, MAX_AVATAR_DIM, MIN_AVATAR_DIM

ImageKind = Literal["png", "jpeg", "webp"]


@dataclass(frozen=True)
class ParsedAvatarImage:
    kind: ImageKind
    width: int
    height: int
    ext: str


class AvatarValidationError(Exception):
    def __init__(self, code: str, message: str, status: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def sniff_image_kind(data: bytes) -> ImageKind | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if len(data) >= 3 and data[0:3] == b"\xff\xd8\xff":
        return "jpeg"
    if len(data) >= 12 and data[0:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def parse_dimensions(data: bytes, kind: ImageKind) -> tuple[int, int] | None:
    if kind == "png":
        return _png_dimensions(data)
    if kind == "jpeg":
        return _jpeg_dimensions(data)
    return _webp_dimensions(data)


def _png_dimensions(data: bytes) -> tuple[int, int] | None:
    if len(data) < 24:
        return None
    if data[12:16] != b"IHDR":
        return None
    width, height = struct.unpack(">II", data[16:24])
    return int(width), int(height)


def _jpeg_dimensions(data: bytes) -> tuple[int, int] | None:
    idx = 2
    length = len(data)
    while idx + 4 < length:
        if data[idx] != 0xFF:
            idx += 1
            continue
        marker = data[idx + 1]
        idx += 2
        if marker in (0xD8, 0x01):
            continue
        if marker in (0xD9, 0xDA):
            break
        if idx + 2 > length:
            return None
        seg_len = struct.unpack(">H", data[idx : idx + 2])[0]
        if seg_len < 2:
            return None
        if marker in (
            0xC0,
            0xC1,
            0xC2,
            0xC3,
            0xC5,
            0xC6,
            0xC7,
            0xC9,
            0xCA,
            0xCB,
            0xCD,
            0xCE,
            0xCF,
        ):
            if idx + 7 > length:
                return None
            height, width = struct.unpack(">HH", data[idx + 3 : idx + 7])
            return int(width), int(height)
        idx += seg_len
    return None


def _webp_dimensions(data: bytes) -> tuple[int, int] | None:
    if len(data) < 16:
        return None
    offset = 12
    length = len(data)
    while offset + 8 <= length:
        tag = data[offset : offset + 4]
        size = struct.unpack("<I", data[offset + 4 : offset + 8])[0]
        chunk_start = offset + 8
        chunk_end = chunk_start + size + (size & 1)
        if tag == b"VP8X" and size >= 10 and chunk_end <= length:
            payload = data[chunk_start : chunk_start + size]
            if len(payload) >= 10:
                width = 1 + int.from_bytes(payload[4:7], "little")
                height = 1 + int.from_bytes(payload[7:10], "little")
                return width, height
        if tag == b"VP8 " and size >= 10 and chunk_end <= length:
            part = data[chunk_start : chunk_start + 10]
            width = struct.unpack("<H", part[6:8])[0] & 0x3FFF
            height = struct.unpack("<H", part[8:10])[0] & 0x3FFF
            return width, height
        if tag == b"VP8L" and size >= 5 and chunk_end <= length:
            bits = data[chunk_start]
            if bits & 0x20:
                return None
            val = int.from_bytes(data[chunk_start + 1 : chunk_start + 5], "little")
            width = (val & 0x3FFF) + 1
            height = ((val >> 14) & 0x3FFF) + 1
            return width, height
        offset = chunk_end
    return None


def validate_avatar_upload(data: bytes) -> ParsedAvatarImage:
    if len(data) > MAX_AVATAR_BYTES:
        raise AvatarValidationError("payload_too_large", "Avatar exceeds 2MB limit.", 413)
    kind = sniff_image_kind(data)
    if kind is None:
        raise AvatarValidationError("unsupported_media_type", "Avatar must be PNG, JPEG, or WebP.", 415)
    dims = parse_dimensions(data, kind)
    if dims is None:
        raise AvatarValidationError("invalid_image", "Could not read image dimensions from header.", 422)
    width, height = dims
    if width < MIN_AVATAR_DIM or height < MIN_AVATAR_DIM:
        raise AvatarValidationError(
            "invalid_dimensions",
            f"Image {width}x{height} is smaller than {MIN_AVATAR_DIM}px minimum.",
            422,
        )
    if width > MAX_AVATAR_DIM or height > MAX_AVATAR_DIM:
        raise AvatarValidationError(
            "invalid_dimensions",
            f"Image {width}x{height} exceeds {MAX_AVATAR_DIM}px maximum.",
            422,
        )
    ratio = max(width, height) / min(width, height)
    if ratio > MAX_AVATAR_ASPECT:
        raise AvatarValidationError(
            "invalid_dimensions",
            f"Image {width}x{height} aspect ratio exceeds {MAX_AVATAR_ASPECT}:1.",
            422,
        )
    ext = {"png": "png", "jpeg": "jpg", "webp": "webp"}[kind]
    return ParsedAvatarImage(kind=kind, width=width, height=height, ext=ext)
